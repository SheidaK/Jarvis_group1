import csv
from pathlib import Path
import argparse

def read_csv_file(file_path):
    with open(file_path, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            print(file_path.name, row)

def main():
    parser = argparse.ArgumentParser(description="Read all CSV files in a folder.")
    parser.add_argument("folder", help="Folder containing CSV files")
    args = parser.parse_args()

    folder = Path(args.folder)

    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")

    csv_files = sorted(folder.glob("*.csv"))

    if not csv_files:
        print(f"No CSV files found in {folder}")
        return

    for csv_file in csv_files:
        print(f"\nReading: {csv_file.name}")
        read_csv_file(csv_file)

if __name__ == "__main__":
    main()