#!/usr/bin/env python3
"""
Convert JPG images (VGA resolution, 12fps) to MP4 videos.
Only converts missing videos by comparing with existing videos in VIDEO folder.
After conversion, deletes ALL contents of IMAGE folder.
Designed to run daily at 12:00 AM via GitHub Actions.
"""

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Set


def setup_directories(base_path: Path) -> Tuple[Path, Path]:
    """
    Ensure IMAGE and VIDEO directories exist.
    
    Args:
        base_path: Base directory (repository root)
    
    Returns:
        Tuple of (IMAGE path, VIDEO path)
    """
    image_dir = base_path / "IMAGE"
    video_dir = base_path / "VIDEO"
    
    image_dir.mkdir(exist_ok=True)
    video_dir.mkdir(exist_ok=True)
    
    return image_dir, video_dir


def find_session_folders(image_dir: Path) -> List[Path]:
    """
    Find all session folders in IMAGE directory matching YYYYMMDD_VID pattern.
    
    Args:
        image_dir: Path to IMAGE directory
    
    Returns:
        List of session folder paths
    """
    pattern = re.compile(r'^\d{8}_VID$')
    folders = [f for f in image_dir.iterdir() if f.is_dir() and pattern.match(f.name)]
    return sorted(folders)


def parse_session_folder(folder_name: str) -> str:
    """
    Extract date from session folder name.
    
    Args:
        folder_name: Folder name like '20260302_VID'
    
    Returns:
        Date string like '20260302'
    """
    return folder_name.split('_')[0]


def get_existing_videos(video_dir: Path) -> Set[Tuple[str, int]]:
    """
    Scan VIDEO folder for existing videos.
    Expected format: YYYYMMDD_VIDEO_sessionnumber.mp4
    
    Args:
        video_dir: Path to VIDEO directory
    
    Returns:
        Set of tuples (date_str, session_number)
    """
    existing_videos = set()
    pattern = re.compile(r'^(\d{8})_VIDEO_(\d+)\.mp4$')
    
    for video_file in video_dir.glob("*.mp4"):
        match = pattern.match(video_file.name)
        if match:
            date_str = match.group(1)
            session_num = int(match.group(2))
            existing_videos.add((date_str, session_num))
    
    return existing_videos


def get_available_sessions(image_dir: Path) -> Dict[str, Set[int]]:
    """
    Scan IMAGE folder to find all available sessions that can be converted.
    
    Args:
        image_dir: Path to IMAGE directory
    
    Returns:
        Dictionary: date_str -> set of session numbers
    """
    available_sessions = {}
    
    for session_folder in find_session_folders(image_dir):
        date_str = parse_session_folder(session_folder.name)
        sessions = get_session_images(session_folder)
        
        if date_str not in available_sessions:
            available_sessions[date_str] = set()
        
        available_sessions[date_str].update(sessions.keys())
    
    return available_sessions


def get_missing_sessions(
    available_sessions: Dict[str, Set[int]],
    existing_videos: Set[Tuple[str, int]]
) -> Dict[str, Set[int]]:
    """
    Determine which sessions need to be converted.
    
    Args:
        available_sessions: Dict of date_str -> available session numbers
        existing_videos: Set of (date_str, session_number) that already have videos
    
    Returns:
        Dict of date_str -> set of missing session numbers
    """
    missing_sessions = {}
    
    for date_str, sessions in available_sessions.items():
        missing = set()
        for session_num in sessions:
            if (date_str, session_num) not in existing_videos:
                missing.add(session_num)
        
        if missing:
            missing_sessions[date_str] = missing
    
    return missing_sessions


def get_session_images(session_folder: Path) -> Dict[int, List[Tuple[int, Path]]]:
    """
    Scan session folder for images and organize by session number.
    Expected format: session_frame.jpg (e.g., 1_1.jpg, 1_2.jpg, 2_1.jpg)
    
    Args:
        session_folder: Path to session folder
    
    Returns:
        Dictionary: session_number -> list of (frame_number, file_path)
    """
    sessions: Dict[int, List[Tuple[int, Path]]] = {}
    
    # Pattern to match session_frame.jpg
    pattern = re.compile(r'^(\d+)_(\d+)\.jpg$', re.IGNORECASE)
    
    for file_path in session_folder.glob("*.jpg"):
        match = pattern.match(file_path.name)
        if match:
            session_num = int(match.group(1))
            frame_num = int(match.group(2))
            
            if session_num not in sessions:
                sessions[session_num] = []
            sessions[session_num].append((frame_num, file_path))
    
    # Sort frames by frame number for each session
    for session_num in sessions:
        sessions[session_num].sort(key=lambda x: x[0])
    
    return sessions


def check_vga_resolution(image_path: Path) -> bool:
    """
    Check if image has VGA resolution (640x480).
    Requires PIL/Pillow library.
    
    Args:
        image_path: Path to image file
    
    Returns:
        True if resolution is 640x480, False otherwise
    """
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            return img.size == (640, 480)
    except ImportError:
        print("Warning: PIL not installed. Skipping resolution check.")
        return True  # Assume valid if PIL not available
    except Exception as e:
        print(f"Warning: Could not check resolution for {image_path}: {e}")
        return False


def images_to_video(
    images: List[Tuple[int, Path]],
    output_path: Path,
    fps: int = 12
) -> bool:
    """
    Convert a list of images to MP4 video using ffmpeg.
    
    Args:
        images: List of (frame_number, image_path)
        output_path: Output video file path
        fps: Frames per second (default: 12)
    
    Returns:
        True if successful, False otherwise
    """
    if not images:
        print(f"No images provided for {output_path}")
        return False
    
    # Create a temporary directory for sequentially numbered images
    temp_dir = output_path.parent / f"temp_{output_path.stem}"
    temp_dir.mkdir(exist_ok=True)
    
    try:
        # Create symbolic links or copy with sequential names
        for idx, (_, img_path) in enumerate(images, start=1):
            # Create link with sequential numbering (e.g., frame_0001.jpg)
            seq_name = f"frame_{idx:04d}.jpg"
            seq_path = temp_dir / seq_name
            
            # Use symlink to avoid copying large files (fallback to copy if symlink fails)
            try:
                os.symlink(img_path.absolute(), seq_path)
            except (OSError, NotImplementedError):
                shutil.copy2(img_path, seq_path)
        
        # Build ffmpeg command for VGA resolution
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",  # Overwrite output file
            "-framerate", str(fps),
            "-pattern_type", "glob",
            "-i", str(temp_dir / "frame_*.jpg"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "medium",
            "-crf", "23",
            "-vf", "scale=640:480",  # Force VGA resolution
            str(output_path)
        ]
        
        # Run ffmpeg
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"  ✓ Successfully created: {output_path.name}")
            return True
        else:
            print(f"  ✗ FFmpeg error for {output_path.name}: {result.stderr[:200]}")
            return False
            
    except Exception as e:
        print(f"  ✗ Error creating video {output_path.name}: {e}")
        return False
    finally:
        # Clean up temporary directory
        shutil.rmtree(temp_dir, ignore_errors=True)


def convert_session_to_videos(
    session_folder: Path,
    video_dir: Path,
    missing_sessions: Set[int],
    fps: int = 12
) -> List[Path]:
    """
    Convert only missing sessions within a date folder to videos.
    
    Args:
        session_folder: Path to session folder (e.g., IMAGE/20260302_VID)
        video_dir: Path to VIDEO directory
        missing_sessions: Set of session numbers that need conversion
        fps: Frames per second
    
    Returns:
        List of created video file paths
    """
    date_str = parse_session_folder(session_folder.name)
    created_videos = []
    
    # Get organized images by session
    all_sessions = get_session_images(session_folder)
    
    if not all_sessions:
        print(f"  No valid images found in {session_folder.name}")
        return created_videos
    
    # Filter to only missing sessions
    sessions_to_convert = {
        session_num: images 
        for session_num, images in all_sessions.items() 
        if session_num in missing_sessions
    }
    
    if not sessions_to_convert:
        print(f"  No missing sessions to convert in {session_folder.name}")
        return created_videos
    
    print(f"\n📁 Processing {session_folder.name}")
    print(f"   Total sessions found: {len(all_sessions)}")
    print(f"   Sessions to convert: {sorted(sessions_to_convert.keys())}")
    
    for session_num in sorted(sessions_to_convert.keys()):
        images = sessions_to_convert[session_num]
        
        # Validate resolution
        valid_images = []
        for frame_num, img_path in images:
            if check_vga_resolution(img_path):
                valid_images.append((frame_num, img_path))
            else:
                print(f"  ⚠ Warning: {img_path.name} does not have VGA resolution (640x480)")
        
        if not valid_images:
            print(f"  ✗ Session {session_num}: No valid VGA images found")
            continue
        
        # Output filename: YYYYMMDD_VIDEO_sessionnumber.mp4
        output_filename = f"{date_str}_VIDEO_{session_num}.mp4"
        output_path = video_dir / output_filename
        
        print(f"  🎬 Converting Session {session_num}: {len(valid_images)} frames -> {output_filename}")
        
        if images_to_video(valid_images, output_path, fps):
            created_videos.append(output_path)
    
    return created_videos


def delete_all_image_contents(image_dir: Path) -> bool:
    """
    Delete ALL contents of IMAGE folder.
    
    Args:
        image_dir: Path to IMAGE directory
    
    Returns:
        True if successful, False otherwise
    """
    if not image_dir.exists():
        print(f"IMAGE folder does not exist: {image_dir}")
        return False
    
    try:
        deleted_count = 0
        for item in image_dir.iterdir():
            if item.is_file():
                item.unlink()
                deleted_count += 1
                print(f"  Deleted file: {item.name}")
            elif item.is_dir():
                shutil.rmtree(item)
                deleted_count += 1
                print(f"  Deleted folder: {item.name}")
        
        if deleted_count > 0:
            print(f"\n✓ Successfully deleted {deleted_count} item(s) from IMAGE folder")
        else:
            print("✓ IMAGE folder was already empty")
        return True
        
    except Exception as e:
        print(f"❌ Error deleting IMAGE contents: {e}")
        return False


def main():
    """Main execution function."""
    print("=" * 60)
    print(f"🎬 IMAGE to VIDEO Converter - Smart Incremental Conversion")
    print(f"📅 Started at: {datetime.now()}")
    print(f"📺 Target Resolution: VGA (640x480)")
    print(f"🎞️ Frame Rate: 12 fps")
    print("=" * 60)
    
    # Set up paths
    repo_root = Path(__file__).parent.resolve()
    image_dir, video_dir = setup_directories(repo_root)
    
    print(f"\n📂 Repository root: {repo_root}")
    print(f"📸 IMAGE directory: {image_dir}")
    print(f"🎥 VIDEO directory: {video_dir}")
    
    # Step 1: Get existing videos
    print("\n" + "=" * 60)
    print("STEP 1: Scanning existing videos...")
    print("=" * 60)
    existing_videos = get_existing_videos(video_dir)
    
    if existing_videos:
        print(f"✓ Found {len(existing_videos)} existing video(s):")
        for date_str, session_num in sorted(existing_videos):
            print(f"  - {date_str}_VIDEO_{session_num}.mp4")
    else:
        print("ℹ No existing videos found. Will convert all available sessions.")
    
    # Step 2: Get available sessions in IMAGE folder
    print("\n" + "=" * 60)
    print("STEP 2: Scanning available image sessions...")
    print("=" * 60)
    available_sessions = get_available_sessions(image_dir)
    
    if not available_sessions:
        print("❌ No session folders found in IMAGE directory.")
        print("   Expected format: YYYYMMDD_VID (e.g., 20260302_VID)")
        sys.exit(0)
    
    total_available = sum(len(sessions) for sessions in available_sessions.values())
    print(f"✓ Found {total_available} available session(s) across {len(available_sessions)} date(s):")
    for date_str in sorted(available_sessions.keys()):
        sessions = sorted(available_sessions[date_str])
        print(f"  - {date_str}: sessions {sessions}")
    
    # Step 3: Determine missing sessions
    print("\n" + "=" * 60)
    print("STEP 3: Determining missing videos...")
    print("=" * 60)
    missing_sessions = get_missing_sessions(available_sessions, existing_videos)
    
    if not missing_sessions:
        print("✅ All videos are already converted! Nothing to do.")
        # Still delete everything if there are files in IMAGE?
        if any(image_dir.iterdir()):
            print("\n⚠ IMAGE folder has files but all videos exist.")
            response = input("Delete IMAGE contents anyway? (y/n): ")
            if response.lower() == 'y':
                delete_all_image_contents(image_dir)
        sys.exit(0)
    
    total_missing = sum(len(sessions) for sessions in missing_sessions.values())
    print(f"🎯 Found {total_missing} missing session(s) to convert:")
    for date_str in sorted(missing_sessions.keys()):
        sessions = sorted(missing_sessions[date_str])
        print(f"  - {date_str}: sessions {sessions}")
    
    # Step 4: Convert missing sessions
    print("\n" + "=" * 60)
    print("STEP 4: Converting missing sessions...")
    print("=" * 60)
    
    all_created_videos = []
    
    for session_folder in find_session_folders(image_dir):
        date_str = parse_session_folder(session_folder.name)
        
        if date_str in missing_sessions:
            created_videos = convert_session_to_videos(
                session_folder, 
                video_dir, 
                missing_sessions[date_str],
                fps=12
            )
            all_created_videos.extend(created_videos)
    
    # Step 5: Summary
    print("\n" + "=" * 60)
    print("CONVERSION SUMMARY")
    print("=" * 60)
    
    if all_created_videos:
        print(f"✅ Successfully created {len(all_created_videos)} new video(s):")
        for video in all_created_videos:
            file_size = video.stat().st_size / (1024 * 1024)  # Size in MB
            print(f"  📹 {video.name} ({file_size:.2f} MB)")
        
        # Step 6: DELETE EVERYTHING IN IMAGE FOLDER
        print("\n" + "=" * 60)
        print("STEP 5: Deleting ALL contents of IMAGE folder...")
        print("=" * 60)
        
        if delete_all_image_contents(image_dir):
            print("\n✅ IMAGE folder has been completely cleared!")
        else:
            print("\n⚠ Failed to delete some contents from IMAGE folder")
    else:
        print("❌ No videos were created. IMAGE folder will NOT be deleted.")
    
    # Final status
    print("\n" + "=" * 60)
    print("FINAL STATUS")
    print("=" * 60)
    
    # Check if IMAGE folder is empty
    remaining_items = list(image_dir.iterdir())
    if not remaining_items:
        print("✅ IMAGE folder is empty")
    else:
        print(f"⚠ IMAGE folder still has {len(remaining_items)} item(s)")
        for item in remaining_items:
            print(f"  - {item.name}")
    
    # Show videos created
    video_count = len(list(video_dir.glob("*.mp4")))
    print(f"📹 Total videos in VIDEO folder: {video_count}")
    
    print("\n" + "=" * 60)
    print(f"🏁 Process completed at {datetime.now()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
