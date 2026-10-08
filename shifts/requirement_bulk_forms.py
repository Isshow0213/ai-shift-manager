from django import forms
from django.forms import BaseFormSet, formset_factory


DAY_TYPES = (
    ("weekday", "平日"),
    ("holiday_eve", "祝前日"),
    ("holiday", "休日"),
)


class BulkRequirementForm(forms.Form):
    start_date = forms.DateField(label="開始日", widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(label="終了日", widget=forms.DateInput(attrs={"type": "date"}))
    weekend_policy = forms.ChoiceField(
        label="休日に含める曜日",
        choices=(("sat_sun", "土曜・日曜＋日本の祝日"), ("sun", "日曜＋日本の祝日")),
        initial="sat_sun",
    )
    mode = forms.ChoiceField(
        label="保存方法",
        choices=(("update", "登録・更新（他の時間帯は残す）"), ("replace", "対象日の時間帯をすべて置き換える")),
        initial="update",
    )
    categories = forms.MultipleChoiceField(
        label="設定する区分", choices=DAY_TYPES,
        widget=forms.CheckboxSelectMultiple,
        initial=[key for key, _ in DAY_TYPES],
        error_messages={"required": "設定する区分を1つ以上選んでください。"},
    )

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get("start_date")
        end = cleaned_data.get("end_date")
        if start and end:
            if end < start:
                self.add_error("end_date", "終了日は開始日以降にしてください。")
            elif (end - start).days >= 366:
                self.add_error("end_date", "一度に設定できる期間は366日以内です。")
            if start.year < 1949 or end.year > 2099:
                raise forms.ValidationError("祝日判定に対応する1949年〜2099年の期間を指定してください。")
        return cleaned_data


class RequirementTimeSlotForm(forms.Form):
    start_time = forms.TimeField(label="開始時刻", widget=forms.TimeInput(attrs={"type": "time"}))
    end_time = forms.TimeField(label="終了時刻", widget=forms.TimeInput(attrs={"type": "time"}))
    required_staff_count = forms.IntegerField(
        label="必要人数", min_value=1, max_value=2147483647,
        widget=forms.NumberInput(attrs={"min": 1, "max": 2147483647}),
    )

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get("start_time")
        end = cleaned_data.get("end_time")
        if start and end and start >= end:
            self.add_error("end_time", "終了時刻は開始時刻より後にしてください。")
        return cleaned_data


class BaseRequirementTimeSlotFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        slots = sorted(
            (form.cleaned_data for form in self.forms if form.cleaned_data),
            key=lambda slot: slot["start_time"],
        )
        if not slots:
            raise forms.ValidationError("この区分の時間帯を1つ以上入力してください。")
        for previous, current in zip(slots, slots[1:]):
            if previous["end_time"] > current["start_time"]:
                raise forms.ValidationError("同じ区分の時間帯が重ならないようにしてください。")


RequirementTimeSlotFormSet = formset_factory(
    RequirementTimeSlotForm, formset=BaseRequirementTimeSlotFormSet,
    extra=1, max_num=24, validate_max=True, absolute_max=48,
)
