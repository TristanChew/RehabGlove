#!/usr/bin/env python3
"""
Sync latest 3 session folders from Google Drive to GitHub IMAGE folder.
Uses Google Service Account for authentication (no OAuth required).
Scans Google Drive folder 'FYP Rehab Glove Vid' for YYYYMMDD_VID folders,
selects the 3 newest ones, and syncs them to the IMAGE folder.
Designed to run daily at 10:00 PM via GitHub Actions.
"""

import os
import re
import shutil
import io
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError


# Google Drive API setup
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# Configuration
DRIVE_FOLDER_NAME = "FYP Rehab Glove Vid"
DRIVE_FOLDER_ID = "1xqdps07T1a2aSRLhyWQONWwpBA8uFBml"  # Parent folder ID
DATE_PATTERN = re.compile(r'^(\d{8})_VID$')


class GoogleDriveSync:
    """Handles Google Drive operations for syncing session folders using Service Account."""
    
    def __init__(self, credentials_path: str = 'credentials.json'):
        """
        Initialize Google Drive service with Service Account.
        
        Args:
            credentials_path: Path to service account credentials.json file
        """
        self.credentials_path = credentials_path
        self.service = self.authenticate()
    
    def authenticate(self):
        """Authenticate with Google Drive API using Service Account."""
        try:
            creds = service_account.Credentials.from_service_account_file(
                self.credentials_path, scopes=SCOPES
            )
            service = build('drive', 'v3', credentials=creds)
            print("✓ Successfully authenticated with Google Drive Service Account")
            return service
        except Exception as e:
            print(f"❌ Failed to authenticate: {e}")
            raise
    
    def find_date_folders(self, parent_folder_id: str) -> List[Tuple[str, str, str]]:
        """
        Find all YYYYMMDD_VID folders in the parent folder.
        
        Args:
            parent_folder_id: Google Drive folder ID to search in
        
        Returns:
            List of tuples (folder_name, folder_id, date_string)
        """
        date_folders = []
        
        try:
            # List all folders in the parent directory
            results = self.service.files().list(
                q=f"'{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="files(id, name)",
                pageSize=1000
            ).execute()
            
            folders = results.get('files', [])
            
            for folder in folders:
                folder_name = folder['name']
                match = DATE_PATTERN.match(folder_name)
                if match:
                    date_str = match.group(1)
                    date_folders.append((folder_name, folder['id'], date_str))
            
            # Sort by date (newest first)
            date_folders.sort(key=lambda x: x[2], reverse=True)
            
            print(f"✓ Found {len(date_folders)} date folder(s)")
            
        except HttpError as error:
            print(f"❌ Error listing folders: {error}")
        
        return date_folders
    
    def download_folder_recursive(self, folder_id: str, local_path: Path, folder_name: str) -> bool:
        """
        Recursively download all JPG files from a Google Drive folder.
        
        Args:
            folder_id: Google Drive folder ID
            local_path: Local path to download to
            folder_name: Name of the folder (for logging)
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Create local folder
            local_path.mkdir(parents=True, exist_ok=True)
            
            # Query all files in the folder
            results = self.service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="files(id, name, mimeType)",
                pageSize=1000
            ).execute()
            
            files = results.get('files', [])
            
            if not files:
                print(f"  ⚠ No files found in {folder_name}")
                return True
            
            downloaded_count = 0
            for file in files:
                file_id = file['id']
                file_name = file['name']
                
                # Skip if not a JPEG image
                if not file_name.lower().endswith('.jpg'):
                    continue
                
                file_path = local_path / file_name
                
                # Download file
                request = self.service.files().get_media(fileId=file_id)
                
                with io.FileIO(str(file_path), 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()
                
                downloaded_count += 1
                if downloaded_count % 10 == 0:
                    print(f"    Downloaded {downloaded_count} files...")
            
            print(f"  ✓ Downloaded {downloaded_count} images from {folder_name}")
            return True
            
        except HttpError as error:
            print(f"  ❌ Error downloading folder {folder_name}: {error}")
            return False


def setup_directories(base_path: Path) -> Path:
    """
    Ensure IMAGE directory exists.
    
    Args:
        base_path: Base directory (repository root)
    
    Returns:
        Path to IMAGE directory
    """
    image_dir = base_path / "IMAGE"
    image_dir.mkdir(exist_ok=True)
    return image_dir


def clear_image_folder(image_dir: Path) -> bool:
    """
    Delete all contents of IMAGE folder.
    
    Args:
        image_dir: Path to IMAGE directory
    
    Returns:
        True if successful, False otherwise
    """
    if not image_dir.exists():
        return True
    
    try:
        deleted_count = 0
        for item in image_dir.iterdir():
            if item.is_file():
                item.unlink()
                deleted_count += 1
            elif item.is_dir():
                shutil.rmtree(item)
                deleted_count += 1
        
        if deleted_count > 0:
            print(f"✓ Cleared IMAGE folder: {deleted_count} items removed")
        else:
            print("✓ IMAGE folder already empty")
        return True
        
    except Exception as e:
        print(f"❌ Error clearing IMAGE folder: {e}")
        return False


def copy_downloaded_folders(source_dir: Path, dest_dir: Path, folder_names: List[str]) -> List[str]:
    """
    Copy downloaded folders to IMAGE directory.
    
    Args:
        source_dir: Temporary directory containing downloaded folders
        dest_dir: Destination IMAGE directory
        folder_names: Names of folders to copy
    
    Returns:
        List of successfully copied folder names
    """
    copied_folders = []
    
    for folder_name in folder_names:
        source_path = source_dir / folder_name
        dest_path = dest_dir / folder_name
        
        if not source_path.exists():
            print(f"⚠ Source folder not found: {folder_name}")
            continue
        
        try:
            # Remove destination if it exists
            if dest_path.exists():
                shutil.rmtree(dest_path)
            
            # Copy folder recursively
            shutil.copytree(source_path, dest_path)
            copied_folders.append(folder_name)
            print(f"✓ Copied {folder_name} to IMAGE folder")
            
        except Exception as e:
            print(f"❌ Error copying {folder_name}: {e}")
    
    return copied_folders


def validate_folder_structure(image_dir: Path) -> bool:
    """
    Validate that copied folders have the correct structure.
    
    Args:
        image_dir: Path to IMAGE directory
    
    Returns:
        True if all folders are valid, False otherwise
    """
    all_valid = True
    pattern = re.compile(r'^\d{8}_VID$')
    filename_pattern = re.compile(r'^\d+_\d+\.jpg$', re.IGNORECASE)
    
    for folder in image_dir.iterdir():
        if folder.is_dir():
            if not pattern.match(folder.name):
                print(f"⚠ Invalid folder name format: {folder.name}")
                all_valid = False
                continue
            
            # Check for image files
            jpg_files = list(folder.glob("*.jpg"))
            if not jpg_files:
                print(f"⚠ No JPG files found in {folder.name}")
                all_valid = False
            else:
                # Validate filename pattern
                invalid_files = [f for f in jpg_files if not filename_pattern.match(f.name)]
                if invalid_files:
                    print(f"⚠ Invalid filenames in {folder.name}: {[f.name for f in invalid_files[:5]]}")
                    if len(invalid_files) > 5:
                        print(f"   ... and {len(invalid_files) - 5} more")
                    all_valid = False
                else:
                    # Check for session/frame numbering
                    sessions = set()
                    frames_by_session = {}
                    for jpg in jpg_files:
                        parts = jpg.stem.split('_')
                        if len(parts) == 2:
                            session = int(parts[0])
                            frame = int(parts[1])
                            sessions.add(session)
                            if session not in frames_by_session:
                                frames_by_session[session] = []
                            frames_by_session[session].append(frame)
                    
                    print(f"  ✓ {folder.name}: {len(jpg_files)} images, {len(sessions)} session(s)")
                    for session in sorted(sessions):
                        frames = sorted(frames_by_session[session])
                        print(f"    - Session {session}: {len(frames)} frame(s) (1-{max(frames)})")
    
    return all_valid


def main():
    """Main execution function."""
    print("=" * 70)
    print(f"🔄 Google Drive to GitHub Sync - Daily Image Update")
    print(f"📅 Started at: {datetime.now()}")
    print(f"🎯 Target: {DRIVE_FOLDER_NAME} (latest 3 folders)")
    print("=" * 70)
    
    # Set up paths
    repo_root = Path(__file__).parent.resolve()
    image_dir = setup_directories(repo_root)
    
    print(f"\n📂 Repository root: {repo_root}")
    print(f"📸 IMAGE directory: {image_dir}")
    
    # Initialize Google Drive sync with Service Account
    print("\n" + "=" * 70)
    print("STEP 1: Connecting to Google Drive with Service Account...")
    print("=" * 70)
    
    try:
        # Check if credentials file exists
        credentials_file = Path('credentials.json')
        if not credentials_file.exists():
            print(f"❌ credentials.json not found at {credentials_file.absolute()}")
            print("\nPlease ensure credentials.json is in the repository root.")
            print("To set up service account:")
            print("1. Go to Google Cloud Console")
            print("2. Create a Service Account")
            print("3. Download the JSON key file")
            print("4. Save it as 'credentials.json'")
            print("5. Share your Google Drive folder with the service account email")
            sys.exit(1)
        
        drive_sync = GoogleDriveSync('credentials.json')
    except Exception as e:
        print(f"❌ Failed to initialize Google Drive sync: {e}")
        sys.exit(1)
    
    # Find date folders in the specified Drive folder
    print("\n" + "=" * 70)
    print(f"STEP 2: Scanning for date folders in Drive ID: {DRIVE_FOLDER_ID}...")
    print("=" * 70)
    
    date_folders = drive_sync.find_date_folders(DRIVE_FOLDER_ID)
    
    if not date_folders:
        print("❌ No YYYYMMDD_VID folders found in Google Drive")
        print(f"   Please verify folder ID: {DRIVE_FOLDER_ID}")
        print("   Folder names should match pattern: 20260302_VID")
        sys.exit(0)
    
    print(f"\nFound {len(date_folders)} date folder(s):")
    for folder_name, _, date_str in date_folders[:10]:  # Show first 10
        print(f"  📁 {folder_name} (Date: {date_str})")
    
    if len(date_folders) > 10:
        print(f"  ... and {len(date_folders) - 10} more")
    
    # Get the 3 newest folders
    newest_folders = date_folders[:3]
    
    print("\n" + "=" * 70)
    print("STEP 3: Selecting 3 newest folders to sync...")
    print("=" * 70)
    
    for folder_name, _, date_str in newest_folders:
        print(f"  ✓ {folder_name} (Date: {date_str})")
    
    # Download selected folders to temp directory
    print("\n" + "=" * 70)
    print("STEP 4: Downloading folders from Google Drive...")
    print("=" * 70)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        downloaded_folders = []
        
        for idx, (folder_name, folder_id, date_str) in enumerate(newest_folders, 1):
            print(f"\n[{idx}/{len(newest_folders)}] 📥 Downloading: {folder_name}")
            local_folder_path = temp_path / folder_name
            
            if drive_sync.download_folder_recursive(folder_id, local_folder_path, folder_name):
                downloaded_folders.append(folder_name)
            else:
                print(f"  ⚠ Failed to download {folder_name}, skipping...")
        
        if not downloaded_folders:
            print("\n❌ No folders were successfully downloaded")
            sys.exit(1)
        
        # Clear IMAGE folder
        print("\n" + "=" * 70)
        print("STEP 5: Preparing IMAGE folder...")
        print("=" * 70)
        
        if not clear_image_folder(image_dir):
            print("❌ Failed to clear IMAGE folder")
            sys.exit(1)
        
        # Copy downloaded folders to IMAGE
        print("\n" + "=" * 70)
        print("STEP 6: Copying folders to IMAGE...")
        print("=" * 70)
        
        copied_folders = copy_downloaded_folders(temp_path, image_dir, downloaded_folders)
        
        if not copied_folders:
            print("❌ No folders were copied to IMAGE")
            sys.exit(1)
        
        # Validate structure
        print("\n" + "=" * 70)
        print("STEP 7: Validating folder structure...")
        print("=" * 70)
        
        if validate_folder_structure(image_dir):
            print("✓ Folder structure validation passed")
        else:
            print("⚠ Folder structure validation found issues (but continuing)")
        
        # Summary
        print("\n" + "=" * 70)
        print("SYNC SUMMARY")
        print("=" * 70)
        print(f"✅ Successfully synced {len(copied_folders)} folder(s) to IMAGE:")
        
        total_images = 0
        for folder_name in copied_folders:
            folder_path = image_dir / folder_name
            jpg_count = len(list(folder_path.glob("*.jpg")))
            total_images += jpg_count
            print(f"  📁 {folder_name} - {jpg_count} images")
        
        print(f"\n📊 Total images synced: {total_images}")
        
        # Show missing if any
        if len(newest_folders) > len(copied_folders):
            print(f"\n⚠ Failed to download {len(newest_folders) - len(copied_folders)} folder(s)")
    
    print("\n" + "=" * 70)
    print(f"🏁 Sync completed at {datetime.now()}")
    print("=" * 70)


if __name__ == "__main__":
    main()
