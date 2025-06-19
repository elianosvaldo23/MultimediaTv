#!/usr/bin/env python3
"""
Test script to verify all dependencies can be imported correctly
"""

def test_imports():
    """Test importing all required modules"""
    try:
        print("Testing imports...")
        
        # Core dependencies
        import telegram
        print("✓ python-telegram-bot imported successfully")
        
        import requests
        print("✓ requests imported successfully")
        
        import bs4
        print("✓ beautifulsoup4 imported successfully")
        
        import imdb
        print("✓ imdbpy imported successfully")
        
        import flask
        print("✓ flask imported successfully")
        
        import dotenv
        print("✓ python-dotenv imported successfully")
        
        import yt_dlp
        print("✓ yt-dlp imported successfully")
        
        import ffmpeg
        print("✓ ffmpeg-python imported successfully")
        
        import dropbox
        print("✓ dropbox imported successfully")
        
        print("\n✅ All imports successful!")
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

if __name__ == "__main__":
    test_imports()
