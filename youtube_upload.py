#!/usr/bin/env python
import os
import pickle
import argparse
import glob
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request

def get_authenticated_service(token_path):
    """Load credentials from the saved token file."""
    if not os.path.exists(token_path):
        raise FileNotFoundError(f"Token file {token_path} not found. Run youtube_auth.py first.")
    
    with open(token_path, 'rb') as token:
        credentials = pickle.load(token)
    
    # Refresh token if expired
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        with open(token_path, 'wb') as token:
            pickle.dump(credentials, token)
    
    return build('youtube', 'v3', credentials=credentials)

def upload_video(youtube, file_path, title, description, category_id, tags, privacy_status="private"):
    """Upload a video to YouTube."""
    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': category_id
        },
        'status': {
            'privacyStatus': privacy_status
        }
    }

    # Call the API's videos.insert method to create and upload the video
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)
    
    print(f"Uploading video: {title}...")
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media
    )
    
    response = request.execute()
    print(f"Upload complete! Video ID: {response['id']}")
    print(f"Video URL: https://www.youtube.com/watch?v={response['id']}")
    return response

def list_available_tokens():
    """List all available token folders and token files."""
    token_folders = [d for d in os.listdir('.') if os.path.isdir(d) and d.startswith('tokens_')]
    
    if not token_folders:
        print("No token folders found. Run youtube_auth.py first to create authentication tokens.")
        return []
    
    print("\nAvailable channel tokens:")
    token_files = []
    
    for i, folder in enumerate(token_folders, 1):
        tokens_in_folder = glob.glob(os.path.join(folder, '*.pickle'))
        if tokens_in_folder:
            token_path = tokens_in_folder[0]  # Get the first pickle file in the folder
            token_files.append(token_path)
            print(f"{i}. {folder} -> {os.path.basename(token_path)}")
    
    if not token_files:
        print("No token files found in the token folders. Run youtube_auth.py first.")
    
    return token_files

def main():
    parser = argparse.ArgumentParser(description='Upload a video to YouTube')
    parser.add_argument('--token', help='Token file or folder to use (leave empty to choose from list)')
    parser.add_argument('--file', help='Video file to upload')
    parser.add_argument('--title', help='Video title')
    parser.add_argument('--description', help='Video description')
    parser.add_argument('--category', default='22', help='Video category ID (default: 22 for People & Blogs)')
    parser.add_argument('--tags', help='Comma-separated tags')
    parser.add_argument('--privacy', default='private', 
                       choices=['private', 'public', 'unlisted'],
                       help='Privacy status (default: private)')
    
    args = parser.parse_args()
    
    # List available tokens if not specified
    token_files = list_available_tokens()
    if not token_files:
        return
    
    token_path = args.token
    if not token_path:
        if len(token_files) == 1:
            token_path = token_files[0]
            print(f"Using the only available token: {token_path}")
        else:
            choice = input("\nSelect a token number from the list above: ")
            try:
                index = int(choice) - 1
                if 0 <= index < len(token_files):
                    token_path = token_files[index]
                else:
                    print("Invalid selection. Exiting.")
                    return
            except ValueError:
                print("Invalid input. Exiting.")
                return
    
    # Check if token file exists
    if not os.path.exists(token_path):
        print(f"Token file {token_path} not found.")
        return
    
    # Get video details if not provided
    file_path = args.file
    if not file_path:
        file_path = input("Enter the path to the video file: ")
        if not os.path.exists(file_path):
            print(f"File {file_path} not found. Exiting.")
            return
    
    title = args.title
    if not title:
        title = input("Enter video title: ")
    
    description = args.description
    if not description:
        description = input("Enter video description: ")
    
    tags = args.tags
    if not tags:
        tags_input = input("Enter comma-separated tags (or press Enter for none): ")
        tags = tags_input.split(',') if tags_input else []
    else:
        tags = tags.split(',')
    
    # Initialize the YouTube service
    youtube = get_authenticated_service(token_path)
    
    # Upload the video
    upload_video(
        youtube, 
        file_path, 
        title, 
        description, 
        args.category, 
        tags, 
        args.privacy
    )

if __name__ == "__main__":
    main()
