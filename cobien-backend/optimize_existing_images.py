import os
import io
from pymongo import MongoClient
import gridfs
from PIL import Image

def optimize_image_bytes(image_bytes, max_size=(300, 300)):
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        out_buf = io.BytesIO()
        img.save(out_buf, format="JPEG", quality=85, optimize=True)
        return out_buf.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"  Error compressing: {e}")
        return None, None

def migrate_collection(db, col_name):
    fs_bucket = gridfs.GridFS(db, collection=col_name)
    files_col = db[f"{col_name}.files"]
    
    # Find all unique filenames
    filenames = files_col.distinct("filename")
    print(f"\nProcessing collection: {col_name} ({len(filenames)} unique files)")
    
    for filename in filenames:
        if not filename:
            continue
        try:
            # Get last version
            grid_out = fs_bucket.get_last_version(filename=filename)
            original_size = grid_out.length
            
            # Read bytes
            image_bytes = grid_out.read()
            
            print(f"File '{filename}' ({original_size / 1024:.1f} KB):")
            
            # Compress
            opt_bytes, content_type = optimize_image_bytes(image_bytes)
            if opt_bytes is None:
                continue
                
            new_size = len(opt_bytes)
            if new_size >= original_size:
                print(f"  Optimized size ({new_size / 1024:.1f} KB) is not smaller than original. Skipping.")
                continue
                
            # Delete all old versions of this file
            for doc in files_col.find({"filename": filename}, {"_id": 1}):
                fs_bucket.delete(doc["_id"])
                
            # Put the new optimized version
            fs_bucket.put(opt_bytes, filename=filename, contentType=content_type)
            print(f"  SUCCESS: {original_size / 1024:.1f} KB -> {new_size / 1024:.1f} KB (saved {(original_size - new_size) / 1024:.1f} KB)")
        except Exception as e:
            print(f"  Failed to process '{filename}': {e}")

def main():
    mongo_uri = os.getenv("MONGO_URI", "mongodb+srv://usuarioCoBien:passwordCoBien@clustercobienevents.j8ev5.mongodb.net/?retryWrites=true&w=majority&appName=ClusterCoBienEvents")
    db_name = os.getenv("DB_NAME", "LabasAppDB")
    
    print(f"Connecting to MongoDB at {db_name}...")
    client = MongoClient(mongo_uri)
    db = client[db_name]
    
    migrate_collection(db, "pizarra_contacts_fs")
    migrate_collection(db, "pizarra_people_fs")
    
    print("\nAll done!")

if __name__ == "__main__":
    main()
