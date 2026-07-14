import os
import sys
import io
import bson
from PIL import Image
import pymongo
import gridfs

MONGO_URI = "mongodb+srv://usuarioCoBien:passwordCoBien@clustercobienevents.j8ev5.mongodb.net/?retryWrites=true&w=majority&appName=ClusterCoBienEvents"

def main():
    print("Connecting to MongoDB Atlas...")
    client = pymongo.MongoClient(MONGO_URI)
    db = client["LabasAppDB"]
    print(f"Connected to database: {db.name}")

    buckets = [
        {"name": "pizarra_fs", "fs": gridfs.GridFS(db, "pizarra_fs")},
        {"name": "pizarra_contacts_fs", "fs": gridfs.GridFS(db, "pizarra_contacts_fs")},
        {"name": "pizarra_people_fs", "fs": gridfs.GridFS(db, "pizarra_people_fs")}
    ]

    for bucket in buckets:
        name = bucket["name"]
        fs_bucket = bucket["fs"]
        files_col = db[f"{name}.files"]
        
        print(f"\n=== Processing bucket: {name} ===")
        all_files = list(files_col.find({}))
        print(f"Found {len(all_files)} files.")
        
        updated_count = 0
        skipped_count = 0
        failed_count = 0
        total_saved_bytes = 0

        for file_doc in all_files:
            file_id = file_doc["_id"]
            filename = file_doc.get("filename", "unknown")
            length = file_doc.get("length", 0)
            content_type = file_doc.get("contentType", "")

            # Only process images
            ext = os.path.splitext(filename)[1].lower()
            is_image = ext in ['.jpg', '.jpeg', '.png', '.heic', '.webp'] or 'image' in str(content_type).lower()
            if not is_image:
                skipped_count += 1
                continue

            try:
                # Read original file
                grid_out = fs_bucket.get(file_id)
                raw_bytes = grid_out.read()
                
                # Open with Pillow
                img = Image.open(io.BytesIO(raw_bytes))
                width, height = img.size
                
                # If it's already <= 1024x1024 and less than 150KB, we can skip it to avoid quality loss
                if max(width, height) <= 1024 and length < 150000:
                    skipped_count += 1
                    continue

                print(f"Optimizing {filename} ({width}x{height}, {length/1024:.1f} KB)...")
                
                # Handle orientation if present
                try:
                    from PIL import ImageOps
                    img = ImageOps.exif_transpose(img)
                except Exception:
                    pass

                # Convert mode to RGB if needed
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                
                # Resize
                img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                
                # Compress
                out_buf = io.BytesIO()
                img.save(out_buf, format='JPEG', quality=85, optimize=True)
                opt_bytes = out_buf.getvalue()
                
                # Replace if we saved space or if dimensions changed
                if len(opt_bytes) < length or max(width, height) > 1024:
                    fs_bucket.delete(file_id)
                    fs_bucket.put(opt_bytes, _id=file_id, filename=filename, contentType='image/jpeg')
                    saved = length - len(opt_bytes)
                    total_saved_bytes += saved
                    print(f"  -> Replaced! New size: {len(opt_bytes)/1024:.1f} KB (Saved {saved/1024:.1f} KB)")
                    updated_count += 1
                else:
                    print(f"  -> Skipped (optimized size {len(opt_bytes)/1024:.1f} KB not smaller than original)")
                    skipped_count += 1

            except Exception as e:
                print(f"  -> Error processing {filename}: {e}")
                failed_count += 1

        print(f"Bucket {name} complete: {updated_count} updated, {skipped_count} skipped, {failed_count} failed. Saved {total_saved_bytes/1024/1024:.2f} MB.")

    print("\nAll optimizations complete!")

if __name__ == "__main__":
    main()
