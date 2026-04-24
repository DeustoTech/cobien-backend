import os
from django.shortcuts import redirect
from django.urls import reverse, NoReverseMatch
from pymongo import MongoClient

_client = MongoClient(os.getenv("MONGO_URI"))
_db = _client[os.getenv("DB_NAME", "LabasAppDB")]


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self._forced_url = None

    def _get_forced_url(self):
        if self._forced_url is None:
            try:
                self._forced_url = reverse("change_password_forced")
            except NoReverseMatch:
                self._forced_url = "/accounts/change-password/"
        return self._forced_url

    def __call__(self, request):
        if request.user.is_authenticated:
            forced_url = self._get_forced_url()
            exempt = {forced_url, "/accounts/logout/", "/admin/"}
            if not any(request.path.startswith(p) for p in exempt):
                try:
                    doc = _db["auth_user"].find_one(
                        {"username": request.user.username},
                        {"must_change_password": 1},
                    )
                    if doc and doc.get("must_change_password"):
                        return redirect(forced_url)
                except Exception:
                    pass
        return self.get_response(request)
