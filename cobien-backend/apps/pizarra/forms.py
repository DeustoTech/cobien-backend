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
