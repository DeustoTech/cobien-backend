from django import forms


class PizarraPostForm(forms.Form):
    recipient_key = forms.CharField(max_length=150)
    content = forms.CharField(widget=forms.Textarea, required=False)
    image = forms.ImageField(required=False)

    def clean(self):
        cleaned = super().clean()
        content = cleaned.get("content")
        image = cleaned.get("image")
        if not content and not image:
            raise forms.ValidationError("Escribe un texto o sube una imagen.")
        if image and image.size > 5 * 1024 * 1024:
            raise forms.ValidationError("La imagen no puede superar 5MB.")
        return cleaned


class DeviceAdminForm(forms.Form):
    device_id = forms.CharField(max_length=150)
    display_name = forms.CharField(max_length=150, required=False)
    videocall_room = forms.CharField(max_length=150, required=False)
    enabled = forms.BooleanField(required=False)
    hidden_in_admin = forms.BooleanField(required=False)
    event_visibility_scope = forms.ChoiceField(required=False, choices=(("all", "all"), ("region", "region")))
    event_regions = forms.CharField(widget=forms.Textarea, required=False)

    def clean_device_id(self):
        value = str(self.cleaned_data.get("device_id") or "").strip()
        if not value:
            raise forms.ValidationError("device_id requerido")
        return value

    def clean_display_name(self):
        return str(self.cleaned_data.get("display_name") or "").strip()

    def clean_videocall_room(self):
        return str(self.cleaned_data.get("videocall_room") or "").strip()

    def clean_event_visibility_scope(self):
        value = str(self.cleaned_data.get("event_visibility_scope") or "all").strip().lower()
        return value if value in {"all", "region"} else "all"

    def clean_event_regions(self):
        return str(self.cleaned_data.get("event_regions") or "").strip()


class DeviceContactsAdminForm(forms.Form):
    device_id = forms.CharField(max_length=150)
    display_name = forms.CharField(max_length=150, required=False)
    videocall_room = forms.CharField(max_length=150, required=False)
    enabled = forms.BooleanField(required=False)
    hidden_in_admin = forms.BooleanField(required=False)
    event_visibility_scope = forms.ChoiceField(required=False, choices=(("all", "all"), ("region", "region")))
    event_regions = forms.CharField(widget=forms.Textarea, required=False)
    contacts = forms.CharField(widget=forms.Textarea, required=False)
    assigned_users = forms.CharField(widget=forms.Textarea, required=False)
    default_username = forms.CharField(max_length=150, required=False)

    def clean_device_id(self):
        value = str(self.cleaned_data.get("device_id") or "").strip()
        if not value:
            raise forms.ValidationError("Selecciona un dispositivo.")
        return value

    def clean_display_name(self):
        return str(self.cleaned_data.get("display_name") or "").strip()

    def clean_videocall_room(self):
        return str(self.cleaned_data.get("videocall_room") or "").strip()

    def clean_event_visibility_scope(self):
        value = str(self.cleaned_data.get("event_visibility_scope") or "all").strip().lower()
        return value if value in {"all", "region"} else "all"

    def clean_event_regions(self):
        return str(self.cleaned_data.get("event_regions") or "").strip()

    def clean_default_username(self):
        return str(self.cleaned_data.get("default_username") or "").strip()


class DirectoryPersonForm(forms.Form):
    person_id = forms.CharField(max_length=150, required=False)
    display_name = forms.CharField(max_length=150)
    user_name = forms.CharField(max_length=150)

    def clean_person_id(self):
        return str(self.cleaned_data.get("person_id") or "").strip()

    def clean_display_name(self):
        value = str(self.cleaned_data.get("display_name") or "").strip()
        if not value:
            raise forms.ValidationError("Nombre visible requerido.")
        return value

    def clean_user_name(self):
        value = str(self.cleaned_data.get("user_name") or "").strip()
        if not value:
            raise forms.ValidationError("Nombre/username requerido.")
        return value
