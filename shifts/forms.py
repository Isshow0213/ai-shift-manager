from django import forms
from .models import Availability, Requirement


class AvailabilityForm(forms.ModelForm):
    class Meta:
        model = Availability
        fields = ["work_date", "start_time", "end_time"]
        widgets = {
            "work_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }

class RequirementForm(forms.ModelForm):
    class Meta:
        model = Requirement
        fields = ["work_date", "start_time", "end_time", "required_staff_count"]
        widgets = {
            "work_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "required_staff_count": forms.NumberInput(attrs={"min": 1}),
        }
        labels = {
            "work_date": "日付",
            "start_time": "開始時刻",
            "end_time": "終了時刻",
            "required_staff_count": "必要人数",
        }