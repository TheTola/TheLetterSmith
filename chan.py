# File: chan.py
#!/usr/bin/env python3

import os
import sys
import shutil
from pathlib import Path

# >>> Adjust only if your root changes <<<
ROOT_FOLDER = Path(r"C:\Users\Oluwatola Ayedun\Desktop\SmithLetter")
SOURCE_ICON = ROOT_FOLDER / "gallery" / "icons" / "FSmith.ico"  # master copy in your repo
TARGET_ICON_NAME = "FSmith.ico"  # copied into ROOT and referenced by desktop.ini

INI_CONTENT = (
    "[.ShellClassInfo]\n"
    f"IconResource={TARGET_ICON_NAME},0\n"
    "IconFile=\n"
    "IconIndex=0\n"
    "[ViewState]\n"
    "Mode=\n"
    "Vid=\n"
    "FolderType=Generic\n"
)

def main():
    if not ROOT_FOLDER.exists():
        print(f"❌ Root folder not found: {ROOT_FOLDER}")
        sys.exit(1)

    if not SOURCE_ICON.exists():
        print(f"❌ Source icon not found: {SOURCE_ICON}")
        sys.exit(1)

    # Copy icon to root
    dest_icon = ROOT_FOLDER / TARGET_ICON_NAME
    try:
        shutil.copyfile(str(SOURCE_ICON), str(dest_icon))
        print(f"✅ Copied icon to: {dest_icon}")
    except Exception as e:
        print(f"❌ Failed to copy icon: {e}")
        sys.exit(1)

    # Write desktop.ini in root
    ini_path = ROOT_FOLDER / "desktop.ini"
    try:
        ini_path.write_text(INI_CONTENT, encoding="utf-8")
        print(f"✅ Wrote desktop.ini: {ini_path}")
    except Exception as e:
        print(f"❌ Failed to write desktop.ini: {e}")
        sys.exit(1)

    # Mark attributes so Explorer respects the icon
    try:
        os.system(f'attrib +h +s "{ini_path}"')
        os.system(f'attrib +s "{ROOT_FOLDER}"')
        print("✅ Applied Windows attributes for folder icon.")
    except Exception as e:
        print(f"⚠️ Attribute set warning: {e}")

    print("\nDone. If Explorer doesn’t update immediately, right-click → Refresh, or reopen the window.")

if __name__ == "__main__":
    main()
