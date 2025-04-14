#!/usr/bin/env python3

import os
import json
import sys
import time
import datetime
import argparse
import io
import requests  # Added for Telegram API calls
import shutil  # Added for file cleanup operations
import random  # Added for random video selection
import pickle  # Added for loading credentials from pickle files
import re

# Force UTF-8 encoding for all file operations and console output
sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')

from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.http import MediaIoBaseDownload
import google.oauth2.credentials
import googleapiclient.discovery
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
import httplib2
import http.client
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Constants from Google Drive script
DRIVE_SHEETS_SCOPES = ['https://www.googleapis.com/auth/drive', 
                       'https://www.googleapis.com/auth/spreadsheets']
TARGET_FOLDER_NAME = "News"
SPREADSHEET_ID = '1cg7HZofo9JfQo4cYc6HBI8FnwZbQhCMOAyScR1JHmqg'  # Ensure consistency in variable naming
SHEET_NAME = 'Sheet1'
SPREADSHEET_ID_FILE = 'spreadsheet_id.txt'
MAX_VIDEOS_PER_CHANNEL = 5  # Safety limit for testing
TEMP_DIR = 'temp_download'

# YouTube upload constants
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError, http.client.NotConnected,
                        http.client.IncompleteRead, http.client.ImproperConnectionState,
                        http.client.CannotSendRequest, http.client.CannotSendHeader,
                        http.client.ResponseNotReady, http.client.BadStatusLine)
MAX_RETRIES = 10
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"
VALID_PRIVACY_STATUSES = ("public", "private", "unlisted")
CHANNEL_TOKENS_DIR = 'acces_channel_token'  
CHANNEL_MAPPINGS_FILE = 'channel_names.json'  

# Telegram Bot configuration
TELEGRAM_BOT_TOKEN = "8109148303:AAG1G8iv_uIpz3HpYOZaUknhySXoMYGYlBA"
TELEGRAM_CHAT_ID = "-1002493560505"
TELEGRAM_THREAD_ID = 777
TELEGRAM_NOTIFICATIONS_ENABLED = True  # Set to False to disable notifications

# New spreadsheet columns for tracking uploads
UPLOAD_TRACKING_COLUMNS = [
    'Upload Status',    # Yes/No/Failed
    'Upload Date',      # Timestamp 
    'YouTube URL',      # Full video URL
    'YouTube Channel',  # Channel name used
    'YouTube Video ID', # Video ID
    'Error Message'     # Only populated if failed
]

def get_google_drive_credentials():
    """Get credentials for Google Drive and Sheets API."""
    # Google Drive link for credentials.json
    CREDENTIALS_DRIVE_LINK = "https://drive.google.com/file/d/1_qVNWlRqdawIi9xZTvaPS4fKY6_W9Zfb/view?usp=sharing"
    credentials_file = os.path.join('gd', 'credentials.json')
    
    # Try to download from Google Drive first
    try:
        print("Downloading Google Drive credentials from Google Drive...")
        
        # Convert view URL to direct download URL
        file_id = CREDENTIALS_DRIVE_LINK.split('/d/')[1].split('/view')[0]
        download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        
        # Create temp directory if it doesn't exist
        os.makedirs(TEMP_DIR, exist_ok=True)
        temp_credentials_file = os.path.join(TEMP_DIR, 'temp_credentials.json')
        
        # Download the file
        response = requests.get(download_url)
        if response.status_code == 200:
            with open(temp_credentials_file, 'wb') as f:
                f.write(response.content)
            
            # Load the credentials from the downloaded file
            credentials = service_account.Credentials.from_service_account_file(
                temp_credentials_file, scopes=DRIVE_SHEETS_SCOPES)
            
            # Clean up the temporary file
            os.remove(temp_credentials_file)
            return credentials
        else:
            print(f"Failed to download credentials from Google Drive: {response.status_code}")
            # Fall back to local file
    except Exception as e:
        print(f"Error downloading credentials from Google Drive: {e}")
        # Fall back to local file
    
    # Fall back to local file if download fails
    if os.path.exists(credentials_file):
        print("Using local credentials file...")
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file, scopes=DRIVE_SHEETS_SCOPES)
        return credentials
    else:
        raise FileNotFoundError(f"Google Drive credentials file not found: {credentials_file}")

def get_youtube_credentials(channel_id=None, channel_name=None):
    """Get YouTube credentials based on channel ID or name."""
    print(f"Using channel: {channel_name if channel_name else channel_id}")
    
    # Channel name to Drive file ID mapping
    CHANNEL_DRIVE_LINKS = {
        "NeuroNews": "https://drive.google.com/file/d/1pP_ent6LXlwse5ilLUXbae2s8Npci9yl/view?usp=sharing",
        "ByteReport": "https://drive.google.com/file/d/1_v8pcWe6up9O46QaCPjOMeta2NpMcwIP/view?usp=sharing",
        "InfoPulse": "https://drive.google.com/file/d/1tq2ukw88SheNSRitJbN_An0bbWCe-epn/view?usp=sharing",
        "EchoAI": "https://drive.google.com/file/d/1TH-XZRzTtkAjOmfOYGhGzJMKHdNc6ebO/view?usp=sharing"
    }
    
    if not channel_name and not channel_id:
        print("No channel specified. Please provide channel_id or channel_name.")
        return None
    
    if channel_name:
        if channel_name not in CHANNEL_DRIVE_LINKS:
            print(f"Error: Channel name '{channel_name}' not recognized.")
            print(f"Available channels: {', '.join(CHANNEL_DRIVE_LINKS.keys())}")
            return None
            
        token_url = CHANNEL_DRIVE_LINKS[channel_name]
        
        # Extract file ID from Google Drive URL
        file_id_match = re.search(r'\/d\/([^\/]+)', token_url)
        if not file_id_match:
            print(f"Invalid Drive URL format for channel {channel_name}")
            return None
            
        token_file_id = file_id_match.group(1)
        
        # Download token pickle file from Google Drive
        token_dir = f"tokens_{channel_name}"
        os.makedirs(token_dir, exist_ok=True)
        token_path = os.path.join(token_dir, "token.pickle")
        
        print("Downloading token from Google Drive...")
        credentials = get_google_drive_credentials()
        drive_service = build('drive', 'v3', credentials=credentials)
        
        try:
            request = drive_service.files().get_media(fileId=token_file_id)
            
            with open(token_path, 'wb') as f:
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
            
            # Load credentials from the downloaded pickle file
            with open(token_path, 'rb') as token:
                creds = pickle.load(token)
                
            # Check if token is expired and refresh if needed
            if creds.expired:
                print(f"Refreshing expired token for {channel_name}...")
                creds.refresh(Request())
                # Save the refreshed token
                with open(token_path, 'wb') as token:
                    pickle.dump(creds, token)
            
            return creds
            
        except Exception as e:
            print(f"Error downloading/loading token for {channel_name}: {e}")
            return None
    else:
        print("Channel ID-based authentication not implemented yet.")
        return None

def list_available_youtube_channels():
    """List all channels that have saved YouTube tokens on Google Drive."""
    # Channel token Google Drive links
    CHANNEL_DRIVE_LINKS = {
        "NeuroNews": "https://drive.google.com/file/d/1pP_ent6LXlwse5ilLUXbae2s8Npci9yl/view?usp=sharing",
        "InfoPulse": "https://drive.google.com/file/d/1tq2ukw88SheNSRitJbN_An0bbWCe-epn/view?usp=sharing",
        "EchoAI": "https://drive.google.com/file/d/1TH-XZRzTtkAjOmfOYGhGzJMKHdNc6ebO/view?usp=sharing",
        "ByteReport": "https://drive.google.com/file/d/1_v8pcWe6up9O46QaCPjOMeta2NpMcwIP/view?usp=sharing"
    }
    
    print("\nAvailable YouTube Channels:")
    print("=" * 60)
    
    for i, (channel_name, drive_link) in enumerate(CHANNEL_DRIVE_LINKS.items(), 1):
        print(f"{i}. {channel_name}")
        print(f"   Status: Available on Google Drive")
        print(f"   Drive Link: {drive_link}")
        print()
    
    return CHANNEL_DRIVE_LINKS

def select_channel_interactive():
    """Allow user to select a channel interactively."""
    channels = list_available_youtube_channels()
    
    if not channels:
        return None, None
    
    # Convert to list for easier indexing
    channel_items = list(channels.items())
    
    try:
        choice = int(input("\nEnter the number of the channel to use: "))
        if 1 <= choice <= len(channel_items):
            selected_channel_name, _ = channel_items[choice-1]
            return selected_channel_name, selected_channel_name
    except ValueError:
        pass
    
    print("Invalid selection.")
    return None, None

def get_spreadsheet_data():
    """Get all data from the Google Spreadsheet."""
    credentials = get_google_drive_credentials()
    sheets_service = build('sheets', 'v4', credentials=credentials)
    
    # Get spreadsheet headers
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range='Sheet1!1:1'  # Headers
        ).execute()
        
        headers = result.get('values', [[]])[0]
        
        # Get all spreadsheet data
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range='Sheet1!A2:Z1000'  # All data (adjust range as needed)
        ).execute()
        
        rows = result.get('values', [])
        
        # Convert to list of dictionaries with column headers as keys
        data = []
        for row in rows:
            # Pad row with empty strings if it's shorter than headers
            row_padded = row + [''] * (len(headers) - len(row))
            row_dict = {headers[i]: row_padded[i] for i in range(len(headers))}
            data.append(row_dict)
        
        print(f"Successfully retrieved {len(data)} rows from spreadsheet.")
        
        # Also return headers for easy reference
        return {
            'headers': headers,
            'data': data,
            'row_count': len(data)
        }
    except Exception as e:
        print(f"Error retrieving spreadsheet data: {e}")
        # Create a basic structure if retrieval fails
        return {
            'headers': ['Folder ID', 'Subfolder Name', 'Upload Status', 'YouTube URL', 'YouTube Channel', 'Upload Date'],
            'data': [],
            'row_count': 0
        }

def update_spreadsheet_structure():
    """Update the spreadsheet structure to ensure all required columns exist."""
    print("Updating spreadsheet structure...")
    
    credentials = get_google_drive_credentials()
    sheets_service = build('sheets', 'v4', credentials=credentials)
    
    try:
        # Get current headers
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range='Sheet1!1:1'
        ).execute()
        
        headers = result.get('values', [[]])[0]
        print(f"Current spreadsheet headers: {headers}")
        
        # Check if all required columns exist
        required_headers = ['Folder ID', 'Folder Name', 'Uploaded', 'Upload Date', 'Video URL', 'Channel', 'Video ID', 'Error']
        missing_headers = []
        
        for header in required_headers:
            if header not in headers:
                missing_headers.append(header)
        
        if missing_headers:
            print(f"Adding missing headers to spreadsheet: {missing_headers}")
            
            # Add the missing columns
            new_headers = headers + missing_headers
            
            update_response = sheets_service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range='Sheet1!1:1',
                valueInputOption='RAW',
                body={'values': [new_headers]}
            ).execute()
            
            print(f"Updated spreadsheet headers: {new_headers}")
            return True
        else:
            print("Spreadsheet already has all required headers.")
            return True
            
    except Exception as e:
        print(f"Error updating spreadsheet structure: {e}")
        return False

def download_files_from_folder(folder_id, folder_name):
    """Download all files from a Google Drive folder."""
    credentials = get_google_drive_credentials()
    drive_service = build('drive', 'v3', credentials=credentials)
    
    # Create temporary folder for downloads
    temp_folder = os.path.join(TEMP_DIR, folder_name)
    os.makedirs(temp_folder, exist_ok=True)
    
    try:
        # List all files in the folder
        query = f"'{folder_id}' in parents and trashed = false"
        results = drive_service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name, mimeType, size)',
            pageSize=50  # Limit results to 50 files
        ).execute()
        
        items = results.get('files', [])
        
        if not items:
            print(f"No files found in folder: {folder_name}")
            return []
            
        print(f"Found {len(items)} files in folder {folder_name}")
        
        downloaded_files = []
        
        # Download each file
        for item in items:
            file_id = item['id']
            file_name = item['name']
            mime_type = item['mimeType']
            
            # Skip Google Docs formats (need export)
            if 'google-apps' in mime_type:
                print(f"Skipping Google Docs format file: {file_name}")
                continue
                
            # Download file
            output_path = os.path.join(temp_folder, file_name)
            
            # Video files can be large - show progress
            if mime_type.startswith('video/') or file_name.lower().endswith(('.mp4', '.mov', '.avi')):
                download_with_progress(drive_service, file_id, output_path, file_name)
            else:
                # Smaller files - simple download
                request = drive_service.files().get_media(fileId=file_id)
                with open(output_path, 'wb') as f:
                    downloader = MediaIoBaseDownload(f, request)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()
                
                print(f"Downloading {file_name}: 100%")
            
            downloaded_files.append(output_path)
        
        return downloaded_files
        
    except Exception as e:
        print(f"Error downloading files: {e}")
        return []

def download_with_progress(drive_service, file_id, output_path, file_name):
    """Download a file with progress indication."""
    request = drive_service.files().get_media(fileId=file_id)
    
    with open(output_path, 'wb') as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            progress = int(status.progress() * 100)
            print(f"Downloading {file_name}: {progress}%", end='\r')
        print(f"Downloading {file_name}: 100%")

def read_text_file(file_path, default=""):
    """Read text content from a file, with a default if file doesn't exist."""
    if not os.path.exists(file_path):
        return default
        
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read().strip()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return default

def upload_video_to_youtube(video_path, title, description, tags, category="22", 
                          privacy_status="unlisted", credentials=None, channel_title=None):
    """Upload a video to YouTube using the provided credentials."""
    print(f"Uploading video to {channel_title}...")
    
    if not os.path.exists(video_path):
        print(f"Error: Video file not found: {video_path}")
        return None
        
    youtube = build('youtube', 'v3', credentials=credentials)
    
    # Prepare the request body for the API call
    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': category
        },
        'status': {
            'privacyStatus': privacy_status,
            'selfDeclaredMadeForKids': False
        }
    }
    
    # Call the API to insert (upload) the video
    try:
        # Create a MediaFileUpload object
        media = MediaFileUpload(video_path, 
                              chunksize=1024*1024,
                              resumable=True)
                              
        # Create the insert request
        insert_request = youtube.videos().insert(
            part=','.join(body.keys()),
            body=body,
            media_body=media
        )
        
        # Execute the upload with resumable behavior
        video_id = resumable_upload(insert_request, channel_title)
        return video_id
        
    except HttpError as e:
        print(f"HTTP error uploading video: {e.resp.status} {e.content}")
        return None
    except Exception as e:
        print(f"Error uploading video: {str(e)}")
        return None

def set_thumbnail(youtube, video_id, thumbnail_path):
    """Set a custom thumbnail for a YouTube video."""
    if not os.path.exists(thumbnail_path):
        print(f"Thumbnail file not found: {thumbnail_path}")
        return False
    
    try:
        # Upload the thumbnail
        media = MediaFileUpload(thumbnail_path, mimetype='image/jpeg')
        
        # Set as video thumbnail
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=media
        ).execute()
        
        print(f"Custom thumbnail set for video ID: {video_id}")
        return True
    
    except Exception as e:
        print(f"Error setting thumbnail: {e}")
        return False

def resumable_upload(insert_request, channel_title=None):
    """Execute the resumable upload with retry logic."""
    response = None
    error = None
    retry = 0
    
    # Identify which channel is being used for the upload
    channel_msg = f" to {channel_title}" if channel_title else ""
    
    while response is None:
        try:
            print(f"Uploading video{channel_msg}...")
            status, response = insert_request.next_chunk()
            if response is not None:
                if 'id' in response:
                    video_id = response['id']
                    print(f"Video successfully uploaded! Video ID: {video_id}")
                    print(f"Video URL: https://www.youtube.com/watch?v={video_id}")
                    return video_id
                else:
                    print(f"Upload failed with unexpected response: {response}")
                    return None
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES:
                error = f"A retriable HTTP error {e.resp.status} occurred:\n{e.content}"
            else:
                print(f"HTTP error {e.resp.status} occurred:\n{e.content}")
                raise
        except RETRIABLE_EXCEPTIONS as e:
            error = f"A retriable error occurred: {e}"
            
        if error is not None:
            print(error)
            retry += 1
            if retry > MAX_RETRIES:
                print("No longer attempting to retry.")
                return None
                
            max_sleep = 2 ** retry
            sleep_seconds = random.random() * max_sleep
            print(f"Sleeping {sleep_seconds:.1f} seconds and then retrying...")
            time.sleep(sleep_seconds)
            error = None
    
    return None

def update_spreadsheet_row(row_index, video_id, channel_title, status="Yes", error_message=""):
    """Update a specific row in the spreadsheet with upload details."""
    print(f"Updating spreadsheet row with index: {row_index}")
    
    if row_index is None or row_index < 1:
        print(f"Invalid row index for spreadsheet update: {row_index}. Using row search method instead.")
        # Try to find the row using video ID if provided
        if video_id:
            return update_spreadsheet_by_video_id(video_id, channel_title, status, error_message)
        else:
            print("Cannot update spreadsheet - no row index or video ID provided.")
            return False
        
    credentials = get_google_drive_credentials()
    sheets_service = build('sheets', 'v4', credentials=credentials)
    
    # True row in spreadsheet (accounting for 1-indexing and header row)
    sheet_row = row_index + 1
    
    try:
        # First verify the row exists and get its folder ID for double-checking
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f'Sheet1!A{sheet_row}:B{sheet_row}'
        ).execute()
        
        rows = result.get('values', [])
        if not rows:
            print(f"Error: Row {sheet_row} not found in spreadsheet.")
            return False
            
        # Get current headers to locate the right columns
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range='Sheet1!1:1'
        ).execute()
        
        headers = result.get('values', [[]])[0]
        
        # Find column indices
        uploaded_col = None
        upload_date_col = None
        video_url_col = None
        channel_col = None
        video_id_col = None
        error_col = None
        
        for i, header in enumerate(headers):
            if header == 'Uploaded':
                uploaded_col = i
            elif header == 'Upload Date':
                upload_date_col = i
            elif header == 'Video URL':
                video_url_col = i
            elif header == 'Channel':
                channel_col = i
            elif header == 'Video ID':
                video_id_col = i
            elif header == 'Error':
                error_col = i
        
        # Make sure we have at least the uploaded column
        if uploaded_col is None:
            print("Error: Required 'Uploaded' column not found in spreadsheet.")
            return False
        
        # Current date/time string
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
        
        # Update status
        value_range_body = {"values": [[status]]}
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f'Sheet1!{chr(65 + uploaded_col)}{sheet_row}',
            valueInputOption='RAW',
            body=value_range_body
        ).execute()
        
        # Update upload date if successful
        if status == "Yes" and upload_date_col is not None:
            value_range_body = {"values": [[now]]}
            sheets_service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f'Sheet1!{chr(65 + upload_date_col)}{sheet_row}',
                valueInputOption='RAW',
                body=value_range_body
            ).execute()
        
        # Update video URL if successful
        if status == "Yes" and video_url_col is not None and video_id:
            value_range_body = {"values": [[video_url]]}
            sheets_service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f'Sheet1!{chr(65 + video_url_col)}{sheet_row}',
                valueInputOption='RAW',
                body=value_range_body
            ).execute()
        
        # Update channel
        if channel_col is not None and channel_title:
            value_range_body = {"values": [[channel_title]]}
            sheets_service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f'Sheet1!{chr(65 + channel_col)}{sheet_row}',
                valueInputOption='RAW',
                body=value_range_body
            ).execute()
        
        # Update video ID if successful
        if status == "Yes" and video_id_col is not None and video_id:
            value_range_body = {"values": [[video_id]]}
            sheets_service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f'Sheet1!{chr(65 + video_id_col)}{sheet_row}',
                valueInputOption='RAW',
                body=value_range_body
            ).execute()
        
        # Update error message if failed
        if status == "Failed" and error_col is not None and error_message:
            value_range_body = {"values": [[error_message]]}
            sheets_service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f'Sheet1!{chr(65 + error_col)}{sheet_row}',
                valueInputOption='RAW',
                body=value_range_body
            ).execute()
        
        print(f"Successfully updated spreadsheet row {sheet_row} with status: {status}")
        return True
    
    except Exception as e:
        print(f"Error updating spreadsheet row: {e}")
        return False

def update_spreadsheet_by_video_id(video_id, channel_title, status="Yes", error_message=""):
    """Find a row by video ID and update it, or find it by folder ID from all subfolder data."""
    credentials = get_google_drive_credentials()
    sheets_service = build('sheets', 'v4', credentials=credentials)
    
    try:
        # First get all data to find the row
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range='Sheet1!A:G'  # Get enough columns to include video ID
        ).execute()
        
        values = result.get('values', [])
        
        if not values:
            print("No data found in spreadsheet.")
            return False
        
        # Get headers to find column indexes
        headers = values[0]
        
        # Find column indices
        folder_id_col = 0  # A column
        video_id_col = None
        
        for i, header in enumerate(headers):
            if header == 'Video ID':
                video_id_col = i
                break
        
        if video_id_col is None:
            print("Error: 'Video ID' column not found in spreadsheet.")
            return False
        
        # Look for the row with this video ID
        row_index = None
        for i, row in enumerate(values[1:], 1):  # Skip header, use 1-indexing
            if len(row) > video_id_col and row[video_id_col] == video_id:
                row_index = i
                break
        
        if row_index is not None:
            return update_spreadsheet_row(row_index, video_id, channel_title, status, error_message)
        else:
            print(f"Error: Could not find row with Video ID: {video_id}")
            return False
            
    except Exception as e:
        print(f"Error in update_spreadsheet_by_video_id: {e}")
        return False

def send_telegram_notification(video_id, title, channel_title, folder_name):
    """Send a notification to Telegram when a video is uploaded."""
    if not TELEGRAM_NOTIFICATIONS_ENABLED:
        return
        
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Create custom notification message with emojis
        message = f"""🔥 Hey Boss! 🌱 

Your YouTube farm is growing some GREAT fruits! 🍎🍊🍇

Just uploaded a fresh harvest to {channel_title} 🚜📈

📝 Title: {title}
📂 From: {folder_name}
🔗 Watch here: {video_url}

Your content empire keeps expanding! 💪🌟
#FarmingSuccess #ContentGrowth #DigitalHarvest 🌾"""
        
        # Send message to Telegram
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "message_thread_id": TELEGRAM_THREAD_ID,
            "text": message
        }
        
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            print(f"Telegram notification sent successfully")
        else:
            print(f"Telegram notification failed: {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"Error sending Telegram notification: {e}")

def cleanup_downloaded_files(folder_path):
    """Delete downloaded files after successful upload to save disk space."""
    if not os.path.exists(folder_path):
        return
        
    try:
        print(f"Cleaning up downloaded files in {folder_path}")
        shutil.rmtree(folder_path)
        print(f"Successfully deleted temporary files")
    except Exception as e:
        print(f"Error cleaning up files: {e}")

def process_folder_for_upload(folder_data, row_index, channel_id=None, channel_name=None):
    """Process a single folder for upload to YouTube."""
    folder_id = folder_data.get('Folder ID')
    folder_name = folder_data.get('Subfolder Name')
    
    # Get the row index directly from folder_data if available and valid
    if (row_index is None or row_index <= 0) and 'row_index' in folder_data and folder_data['row_index'] > 0:
        row_index = folder_data['row_index']
        print(f"Using row index {row_index} from folder data")
    
    # As a last resort, find the row index by searching the spreadsheet
    if row_index is None or row_index <= 0:
        row_index = find_row_by_folder_id(folder_id)
        if row_index:
            print(f"Found row index {row_index} by searching spreadsheet")
        else:
            print("Warning: Could not determine row index for updating spreadsheet")
    
    if not folder_id or not folder_name:
        print(f"Invalid folder data: {folder_data}")
        return False
    
    print(f"\nProcessing folder: {folder_name} (ID: {folder_id})")
    
    # Check if already uploaded
    if folder_data.get('Upload Status') == 'Yes' and folder_data.get('YouTube URL'):
        print(f"Folder {folder_name} has already been uploaded to YouTube: {folder_data.get('YouTube URL')}")
        print("Skipping upload.")
        return True
    
    # Create temporary directory for downloads
    temp_folder = os.path.join(TEMP_DIR, folder_name)
    os.makedirs(temp_folder, exist_ok=True)
    
    # Download files from the folder
    files = download_files_from_folder(folder_id, folder_name)
    
    if not files:
        error_msg = f"No files found in folder {folder_name}."
        print(error_msg)
        update_spreadsheet_row(row_index, None, None, "Failed", error_msg)
        return False
    
    # Check for required files
    video_file = None
    thumbnail_file = None
    
    for file_path in files:
        file_name = os.path.basename(file_path).lower()
        
        # Check for video files
        if file_name.endswith(('.mp4', '.mov', '.avi', '.wmv', '.flv', '.mkv')):
            video_file = file_path
            
        # Check for thumbnail files
        elif file_name.endswith(('.jpg', '.jpeg', '.png')):
            thumbnail_file = file_path
    
    if not video_file:
        error_msg = f"No video file found in folder {folder_name}."
        print(error_msg)
        update_spreadsheet_row(row_index, None, None, "Failed", error_msg)
        return False
    
    # Read metadata files if they exist
    title = read_text_file(os.path.join(temp_folder, 'title.txt'), folder_name)
    description = read_text_file(os.path.join(temp_folder, 'description.txt'), f"Video from {folder_name}")
    tags_string = read_text_file(os.path.join(temp_folder, 'tags.txt'), "")
    tags = [tag.strip() for tag in tags_string.split(',')] if tags_string else []
    
    # Get the YouTube channel to upload to
    if not channel_id and not channel_name:
        channel_name = DEFAULT_YOUTUBE_CHANNEL
    
    # Get credentials based on the stored token folders from the memory
    token_folder = None
    if channel_name == "NeuroNews" or channel_name == "ByteReport":
        # These channels use bg.json tokens
        if channel_name == "NeuroNews":
            token_folder = "tokens_bg_bg_channel_1"
        else:
            token_folder = "tokens_bg_bg_channel_2"
    elif channel_name == "InfoPulse" or channel_name == "EchoAI":
        # These channels use cl.json tokens
        if channel_name == "InfoPulse":
            token_folder = "tokens_cl_cl_channel_1"
        else:  
            token_folder = "tokens_cl_cl_channel_2"
    
    # Get credentials for this channel
    credentials = None
    if token_folder and os.path.exists(token_folder):
        token_path = os.path.join(token_folder, "token.pickle")
        if os.path.exists(token_path):
            try:
                with open(token_path, 'rb') as token_file:
                    credentials = pickle.load(token_file)
                print(f"Successfully loaded credentials from {token_path}")
            except Exception as e:
                print(f"Error loading credentials from {token_path}: {e}")
    
    # Fall back to downloading from Drive if token not found locally
    if not credentials:
        credentials = get_youtube_credentials(channel_id, channel_name)
    
    if not credentials:
        error_msg = f"Failed to get credentials for channel: {channel_name or channel_id}"
        print(error_msg)
        update_spreadsheet_row(row_index, None, channel_name, "Failed", error_msg)
        return False
    
    try:
        # Upload the video
        video_id = upload_video_to_youtube(
            video_file,
            title,
            description,
            tags,
            credentials=credentials,
            channel_title=channel_name
        )
        
        if not video_id:
            error_msg = "Upload failed with unknown error."
            print(error_msg)
            update_spreadsheet_row(row_index, None, channel_name, "Failed", error_msg)
            return False
            
        print(f"Video successfully uploaded! Video ID: {video_id}")
        print(f"Video URL: https://www.youtube.com/watch?v={video_id}")
        
        # Set thumbnail if available
        if thumbnail_file:
            try:
                youtube = build('youtube', 'v3', credentials=credentials)
                set_thumbnail(youtube, video_id, thumbnail_file)
            except Exception as e:
                print(f"Error setting thumbnail: {e}")
        
        # Update spreadsheet - this is critical for tracking uploads
        success = update_spreadsheet_row(row_index, video_id, channel_name, "Yes")
        if not success:
            # Try one more time with folder ID search
            print("First spreadsheet update attempt failed. Trying with folder ID lookup...")
            found_row = find_row_by_folder_id(folder_id)
            if found_row:
                success = update_spreadsheet_row(found_row, video_id, channel_name, "Yes")
                if success:
                    print(f"Successfully updated spreadsheet on second attempt using row {found_row}")
                else:
                    print("Second attempt to update spreadsheet also failed.")
            else:
                print("Warning: Failed to update spreadsheet, but video was uploaded successfully.")
        
        # Send notification
        send_telegram_notification(video_id, title, channel_name, folder_name)
        
        # Clean up downloaded files
        cleanup_downloaded_files(temp_folder)
        
        return True
        
    except Exception as e:
        error_msg = f"Upload failed: {str(e)}"
        print(error_msg)
        update_spreadsheet_row(row_index, None, channel_name, "Failed", error_msg)
        return False

def process_unuploaded_videos(channel_id=None, channel_name=None, limit=None, random_selection=False):
    """Process all unuploaded videos from the Google Drive News folder."""
    # Get subfolders directly from Google Drive
    all_subfolders = get_subfolders_from_drive()
    
    if not all_subfolders:
        print("No subfolders found to process. Aborting.")
        return False
    
    # Also try to update the spreadsheet for tracking purposes
    try:
        update_spreadsheet_structure()
    except Exception as e:
        print(f"Warning: Could not update spreadsheet structure: {e}")
        print("Continuing with direct folder processing...")
    
    # Filter out subfolders that have already been uploaded
    unuploaded_subfolders = [
        folder for folder in all_subfolders 
        if folder.get('Upload Status') != 'Yes' or not folder.get('YouTube URL')
    ]
    
    print(f"Found {len(all_subfolders)} total subfolders:")
    print(f"- {len(unuploaded_subfolders)} not yet uploaded")
    print(f"- {len(all_subfolders) - len(unuploaded_subfolders)} already uploaded (skipping)")
    
    if not unuploaded_subfolders:
        print("No new videos to upload. All videos have already been processed.")
        return False
    
    # Apply limit and random selection if specified
    if unuploaded_subfolders:
        if random_selection and limit:
            # Randomly select videos up to the limit
            if limit > len(unuploaded_subfolders):
                limit = len(unuploaded_subfolders)
            
            print(f"Randomly selecting {limit} folders for upload.")
            selected_folders = random.sample(unuploaded_subfolders, limit)
            # Sort by folder name for consistent processing
            selected_folders.sort(key=lambda x: x.get('Subfolder Name', ''))
        else:
            # Take the first N videos based on limit
            selected_folders = unuploaded_subfolders[:limit] if limit else unuploaded_subfolders
        
        print(f"Processing {len(selected_folders)} folders{' (limited by --limit)' if limit else ''}.")
        
        success_count = 0
        fail_count = 0
        
        for idx, folder_data in enumerate(selected_folders):
            print(f"\n============================================================")
            print(f"Processing {idx+1}/{len(selected_folders)}: {folder_data.get('Subfolder Name', '')}")
            print(f"============================================================\n")
            
            result = process_folder_for_upload(folder_data, folder_data.get('row_index', 0), channel_id, channel_name)
            
            if result:
                success_count += 1
            else:
                fail_count += 1
        
        print(f"\n============================================================")
        print(f"Upload Summary")
        print(f"============================================================")
        print(f"Total processed: {success_count + fail_count}")
        print(f"Successful: {success_count}")
        print(f"Failed: {fail_count}")
        print(f"============================================================\n")
        
        return success_count > 0
    else:
        print("No folders found to process.")
        return False

def print_upload_history():
    """Print a summary of all previously uploaded videos."""
    spreadsheet_data = get_spreadsheet_data()
    
    if not spreadsheet_data:
        print("Failed to get spreadsheet data.")
        return
    
    # Filter for uploaded videos
    uploaded_videos = [
        data for data in spreadsheet_data['data']
        if data.get('Upload Status', '') == 'Yes' and data.get('YouTube URL', '')
    ]
    
    if not uploaded_videos:
        print("No previously uploaded videos found in the spreadsheet.")
        return
    
    print("\n============================================================")
    print("Upload History Summary")
    print("============================================================")
    print(f"Found {len(uploaded_videos)} previously uploaded videos:")
    
    for idx, video in enumerate(uploaded_videos):
        folder_name = video.get('Subfolder Name', 'Unknown')
        channel = video.get('YouTube Channel', 'Unknown')
        url = video.get('YouTube URL', 'No URL')
        upload_date = video.get('Upload Date', 'Unknown date')
        
        print(f"{idx+1}. {folder_name} → {channel} | {url} ({upload_date})")
    
    print("============================================================\n")

def find_target_folder_id():
    """Find the target folder ID for the News folder."""
    credentials = get_google_drive_credentials()
    drive_service = build('drive', 'v3', credentials=credentials)
    
    try:
        # Search for the News folder by name
        query = f"name = '{TARGET_FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = drive_service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        
        items = results.get('files', [])
        
        if not items:
            print(f"Error: Could not find folder '{TARGET_FOLDER_NAME}' in Google Drive.")
            return None
        
        # Use the first matching folder
        folder_id = items[0]['id']
        print(f"Found {TARGET_FOLDER_NAME} folder with ID: {folder_id}")
        return folder_id
        
    except Exception as e:
        print(f"Error finding target folder: {e}")
        return None

def get_subfolders_from_drive():
    """Get all subfolders directly from Google Drive and match with spreadsheet history."""
    target_folder_id = find_target_folder_id()
    if not target_folder_id:
        return []
    
    credentials = get_google_drive_credentials()
    drive_service = build('drive', 'v3', credentials=credentials)
    sheets_service = build('sheets', 'v4', credentials=credentials)
    
    try:
        # Get all subfolders in the News folder
        query = f"'{target_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = drive_service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name, createdTime)'
        ).execute()
        
        subfolders = results.get('files', [])
        
        if not subfolders:
            print(f"No subfolders found in {TARGET_FOLDER_NAME} folder.")
            return []
        
        # Get existing spreadsheet data to find row indices and upload status
        try:
            # Get all current data from spreadsheet
            result = sheets_service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range='Sheet1!A:G'  # Get enough columns to include all needed data
            ).execute()
            
            spreadsheet_data = result.get('values', [])
            
            if not spreadsheet_data or len(spreadsheet_data) <= 1:  # Only header or empty
                print("No existing data found in spreadsheet.")
                spreadsheet_data = [['Folder ID', 'Folder Name', 'Uploaded', 'Upload Date', 'Video URL', 'Channel', 'Video ID']]
            
            # Get column indices
            headers = spreadsheet_data[0]
            folder_id_col = 0  # A column
            uploaded_col = None
            video_url_col = None
            
            for i, header in enumerate(headers):
                if header == 'Uploaded':
                    uploaded_col = i
                elif header == 'Video URL':
                    video_url_col = i
            
            # Build lookup map of folder IDs to their data and row index
            folder_map = {}
            for row_idx, row in enumerate(spreadsheet_data[1:], 1):  # Start at 1 for first data row
                if row and len(row) > 0:
                    folder_id = row[0]  # First column is Folder ID
                    
                    is_uploaded = False
                    video_url = ""
                    
                    if uploaded_col is not None and len(row) > uploaded_col:
                        is_uploaded = (row[uploaded_col] == "Yes")
                    
                    if video_url_col is not None and len(row) > video_url_col:
                        video_url = row[video_url_col]
                    
                    folder_map[folder_id] = {
                        'row_index': row_idx,
                        'is_uploaded': is_uploaded,
                        'video_url': video_url
                    }
            
            print(f"Found {len(folder_map)} folder entries in spreadsheet.")
        except Exception as e:
            print(f"Error getting spreadsheet data: {e}")
            folder_map = {}
        
        # Sort by creation time (newest first)
        subfolders.sort(key=lambda x: x.get('createdTime', ''), reverse=True)
        
        # Convert to the format expected by the processing functions
        folder_data = []
        new_folders_count = 0
        uploaded_folders_count = 0
        
        for folder in subfolders:
            folder_id = folder['id']
            folder_name = folder['name']
            
            folder_info = {
                'Folder ID': folder_id,
                'Subfolder Name': folder_name,
            }
            
            # Check if folder exists in spreadsheet and get its status
            if folder_id in folder_map:
                folder_data_from_sheet = folder_map[folder_id]
                folder_info['row_index'] = folder_data_from_sheet['row_index']
                folder_info['Upload Status'] = "Yes" if folder_data_from_sheet['is_uploaded'] else ""
                folder_info['YouTube URL'] = folder_data_from_sheet['video_url']
                
                if folder_data_from_sheet['is_uploaded']:
                    uploaded_folders_count += 1
                    print(f"Folder '{folder_name}' has already been uploaded: {folder_data_from_sheet['video_url']}")
            else:
                # This is a new folder that needs to be added to the spreadsheet
                folder_info['is_new'] = True
                folder_info['Upload Status'] = ""
                folder_info['YouTube URL'] = ""
                new_folders_count += 1
            
            folder_data.append(folder_info)
        
        # Summary output
        total_folders = len(folder_data)
        print(f"Found {total_folders} total subfolders in {TARGET_FOLDER_NAME} folder:")
        print(f"- {new_folders_count} new folders (not yet in spreadsheet)")
        print(f"- {uploaded_folders_count} previously uploaded folders")
        print(f"- {total_folders - new_folders_count - uploaded_folders_count} folders tracked but not yet uploaded")
        
        # Add new folders to the spreadsheet
        if new_folders_count > 0:
            print(f"Adding {new_folders_count} new folders to spreadsheet for tracking...")
            new_folders = [f for f in folder_data if f.get('is_new', False)]
            folder_data = add_new_folders_to_spreadsheet(new_folders, folder_data)
        
        return folder_data
        
    except Exception as e:
        print(f"Error getting subfolders: {e}")
        return []

def add_new_folders_to_spreadsheet(new_folders, all_folders_data):
    """Add new folders to the spreadsheet for tracking and update their row indices."""
    if not new_folders:
        return all_folders_data
    
    credentials = get_google_drive_credentials()
    sheets_service = build('sheets', 'v4', credentials=credentials)
    
    try:
        # Get current spreadsheet data to determine next row number
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range='Sheet1!A:A'
        ).execute()
        
        values = result.get('values', [])
        next_row = len(values) + 1  # +1 because rows are 1-indexed in Sheets API
        
        # Prepare rows to add
        rows_to_append = []
        for folder in new_folders:
            rows_to_append.append([
                folder['Folder ID'],
                folder['Subfolder Name'],
                ''  # Initially empty 'Uploaded' status
            ])
        
        # Add the rows
        if rows_to_append:
            sheets_service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range='Sheet1!A2',  # Start after header
                valueInputOption='RAW',
                body={'values': rows_to_append},
                insertDataOption='INSERT_ROWS'
            ).execute()
            
            print(f"Successfully added {len(rows_to_append)} new folders to spreadsheet.")
            
            # Update row indices in the folder data
            for i, folder in enumerate(new_folders):
                folder_id = folder['Folder ID']
                row_index = next_row + i - 1  # Adjust for 0-based vs. 1-based indexing
                
                # Find and update this folder in the main folder_data list
                for f in all_folders_data:
                    if f.get('Folder ID') == folder_id:
                        f['row_index'] = row_index
                        f.pop('is_new', None)  # Remove the 'is_new' flag
                        break
        
        return all_folders_data
        
    except Exception as e:
        print(f"Error adding new folders to spreadsheet: {e}")
        return all_folders_data

def list_child_folders(drive_service, parent_folder_id, folder_name):
    """Get all child folders of a given folder."""
    query = f"'{parent_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = drive_service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name, createdTime)'
    ).execute()
    
    folders = results.get('files', [])
    
    if not folders:
        print(f"No subfolders found in {folder_name} folder.")
        return []
    
    return folders

def find_row_by_folder_id(folder_id):
    """Find the row index in the spreadsheet for a given folder ID."""
    if not folder_id:
        return None
        
    print(f"Looking up spreadsheet row for folder ID: {folder_id}")
    
    credentials = get_google_drive_credentials()
    sheets_service = build('sheets', 'v4', credentials=credentials)
    
    try:
        # Get all data to find the row with this folder ID
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range='Sheet1!A:B'  # Just get folder IDs and names
        ).execute()
        
        values = result.get('values', [])
        
        if not values:
            print("No data found in spreadsheet.")
            return None
        
        # Start from index 1 to skip the header row and use 1-indexing for the actual sheet row
        for row_index, row in enumerate(values[1:], 1):
            if row and len(row) > 0 and row[0] == folder_id:
                print(f"Found folder ID {folder_id} at row {row_index}")
                return row_index
                
        print(f"Could not find folder ID {folder_id} in spreadsheet.")
        return None
        
    except Exception as e:
        print(f"Error finding row for folder ID: {e}")
        return None

def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description="Upload videos from Google Drive to YouTube")
    
    # Channel selection
    channel_group = parser.add_argument_group("Channel Selection")
    channel_group.add_argument("--list-channels", action="store_true", help="List available channels and exit")
    channel_group.add_argument("--channel-id", help="Channel ID to upload to")
    channel_group.add_argument("--channel-name", help="Channel name to upload to (will try to match)")
    
    # Upload options
    upload_group = parser.add_argument_group("Upload Options")
    upload_group.add_argument("--limit", type=int, help="Limit the number of videos to upload")
    upload_group.add_argument("--folder-name", help="Only upload from a specific folder name")
    upload_group.add_argument("--privacy-status", choices=VALID_PRIVACY_STATUSES, default="public",
                            help="Privacy status for uploaded videos")
    upload_group.add_argument("--random", action="store_true", help="Randomly select videos for upload")
    upload_group.add_argument("--upload-history", action="store_true", help="Print upload history and exit")
    
    args = parser.parse_args()
    
    # Create temp directory if it doesn't exist
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    # Just list channels if requested
    if args.list_channels:
        list_available_youtube_channels()
        return
    
    # Print upload history if requested
    if args.upload_history:
        print_upload_history()
        return
    
    # Process unuploaded videos
    process_unuploaded_videos(
        channel_id=args.channel_id,
        channel_name=args.channel_name,
        limit=args.limit,
        random_selection=args.random
    )

if __name__ == "__main__":
    main()
