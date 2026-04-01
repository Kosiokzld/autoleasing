# -*- coding: utf-8 -*-
"""
AutoLeasing v3.0 - Start Script
This script installs dependencies and starts the application.
"""
import subprocess
import sys
import os

def main():
    print()
    print("  ========================================")
    print("  AutoLeasing v3.0 - Стартиране...")
    print("  ========================================")
    print()

    # Install Flask if missing
    try:
        import flask
        print("  [OK] Flask е инсталиран")
    except ImportError:
        print("  Инсталиране на Flask...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "flask", "--quiet"])
        print("  [OK] Flask е инсталиран")

    # Install openpyxl if missing
    try:
        import openpyxl
        print("  [OK] openpyxl е инсталиран")
    except ImportError:
        print("  Инсталиране на openpyxl...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl", "--quiet"])
        print("  [OK] openpyxl е инсталиран")

    print()
    print("  Стартиране на сървъра...")
    print("  Браузърът ще се отвори автоматично.")
    print("  Не затваряйте този прозорец!")
    print()

    # Change to the script's directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # Run app.py
    try:
        subprocess.run([sys.executable, "app.py"], check=True)
    except KeyboardInterrupt:
        print("\n  Сървърът е спрян.")
    except Exception as e:
        print(f"\n  ГРЕШКА: {e}")
        with open(os.path.join(script_dir, "error_log.txt"), "w", encoding="utf-8") as f:
            import traceback
            f.write(traceback.format_exc())
        print(f"  Детайли за грешката: error_log.txt")

    print()
    input("  Натиснете Enter за изход...")

if __name__ == "__main__":
    main()
