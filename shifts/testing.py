"""明示的に提出済みの勤務希望を用意するテスト用フィクスチャ。"""
from django.utils import timezone

from .models import Availability, ShiftSubmission


def submitted_availability(**fields):
    """個別時間だけでなく、対応する月の提出完了も明示して作成する。"""
    membership = fields.get("membership")
    if membership is not None and fields.get("submission") is None:
        submission, _ = ShiftSubmission.objects.get_or_create(
            membership=membership,
            target_month=fields["work_date"].replace(day=1),
            defaults={
                "submission_mode": membership.submission_mode,
                "is_submitted": True,
                "submitted_at": timezone.now(),
            },
        )
        fields["submission"] = submission
    return Availability.objects.create(**fields)
