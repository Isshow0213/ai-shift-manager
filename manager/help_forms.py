from django import forms

from shifts.models import HelpShift


class HelpShiftForm(forms.ModelForm):
    class Meta:
        model = HelpShift
        fields = ["name", "work_date", "start_time", "end_time", "display_color", "note"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "例：山田 太郎"}),
            "work_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "note": forms.Textarea(attrs={"rows": 3, "placeholder": "例：駅前店からヘルプ"}),
        }
        help_texts = {
            "note": "500文字まで。全体シフト表にも表示されます。",
            "display_color": "ヘルプの勤務を見分ける色を選べます。",
        }

    def __init__(self, *args, store, selected_date=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        self.selected_date = selected_date
        self.instance.store = store
        if selected_date is not None:
            self.fields["work_date"].initial = selected_date

    def clean_work_date(self):
        work_date = self.cleaned_data["work_date"]
        if not 2 <= work_date.year <= 9998:
            raise forms.ValidationError("日付は2年〜9998年の範囲で指定してください。")
        if self.selected_date is not None and work_date != self.selected_date:
            raise forms.ValidationError("カレンダーで選択した日付のシフトを追加してください。")
        return work_date

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get("name")
        work_date = cleaned_data.get("work_date")
        start_time, end_time = cleaned_data.get("start_time"), cleaned_data.get("end_time")
        if start_time is not None and end_time is not None and start_time >= end_time:
            self.add_error("end_time", "終了時刻は開始時刻より後にしてください。")
        elif name and work_date and start_time and end_time:
            overlaps = HelpShift.objects.filter(
                store=self.store, name=name, work_date=work_date,
                start_time__lt=end_time, end_time__gt=start_time,
            )
            if self.instance.pk:
                overlaps = overlaps.exclude(pk=self.instance.pk)
            if overlaps.exists():
                self.add_error(None, "この名前のヘルプには、同じ時間帯に重なるシフトが登録されています。")
        return cleaned_data
