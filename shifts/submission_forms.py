from django import forms

from accounts.models import StoreMembership


class SubmissionSettingsForm(forms.ModelForm):
    class Meta:
        model = StoreMembership
        fields = ["submission_mode"]
        labels = {"submission_mode": "シフト提出方式"}
        widgets = {"submission_mode": forms.RadioSelect}
