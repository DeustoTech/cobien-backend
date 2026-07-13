import os
from pymongo import MongoClient
import gridfs

class MongoProxy:
    def __init__(self):
        self._pid = None
        self._client = None
        self._db = None

    def _get_db(self):
        import os
        current_pid = os.getpid()
        if self._client is None or self._pid != current_pid:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = MongoClient(
                os.getenv("MONGO_URI"),
                connect=False,
                serverSelectionTimeoutMS=3000,
                connectTimeoutMS=3000,
            )
            self._pid = current_pid
            self._db = self._client[os.getenv("DB_NAME", "LabasAppDB")]
        return self._db

    def __getitem__(self, name):
        return self._get_db()[name]

    def __getattr__(self, name):
        return getattr(self._get_db(), name)

db = MongoProxy()

class GridFSProxy:
    def __init__(self, collection):
        self._pid = None
        self._fs = None
        self._collection = collection

    def _get_fs(self):
        import os
        current_pid = os.getpid()
        if self._fs is None or self._pid != current_pid:
            self._fs = gridfs.GridFS(db._get_db(), collection=self._collection)
            self._pid = current_pid
        return self._fs

    def __getattr__(self, name):
        return getattr(self._get_fs(), name)

fs = GridFSProxy("pizarra_fs")
fs_contacts = GridFSProxy("pizarra_contacts_fs")
fs_people = GridFSProxy("pizarra_people_fs")

class CollectionProxy:
    def __init__(self, name):
        self._name = name

    def _get_col(self):
        return db[self._name]

    def __getattr__(self, name):
        return getattr(self._get_col(), name)

    def __getitem__(self, name):
        return self._get_col()[name]

col_messages = CollectionProxy("pizarra_messages")
