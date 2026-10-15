from datetime import date

from django.db import transaction

from accounts.models import Store
from shifts.models import HelpShift, Requirement, Shift
from shifts.eligibility import EligibilityIndex


@transaction.atomic
def generate_shifts_for_store(store_id, work_date):
    if not isinstance(work_date, date):
        raise ValueError("A work date is required to generate shifts.")

    store = Store.objects.get(id=store_id)
    requirements = list(
        Requirement.objects.filter(store=store, work_date=work_date)
    )
    result = {
        "created_count": 0,
        "shortfall_count": 0,
        "requirement_count": len(requirements),
    }

    if not requirements:
        return result

    # 選択日の自動生成分だけ作り直し、手動分は残す。
    Shift.objects.filter(
        store=store,
        work_date=work_date,
        is_generated=True,
    ).delete()

    scheduled_shifts = list(
        Shift.objects.filter(store=store, work_date=work_date).values_list(
            "user_id", "start_time", "end_time"
        )
    )
    help_shifts = list(
        HelpShift.objects.filter(store=store, work_date=work_date).values_list(
            "name", "start_time", "end_time"
        )
    )
    eligibility_index = EligibilityIndex(store, work_date)

    for requirement in requirements:
        covered_users = {
            user_id
            for user_id, start_time, end_time in scheduled_shifts
            if start_time <= requirement.start_time
            and end_time >= requirement.end_time
        }
        covered_helpers = {
            name
            for name, start_time, end_time in help_shifts
            if start_time <= requirement.start_time
            and end_time >= requirement.end_time
        }
        remaining_count = max(
            0,
            requirement.required_staff_count
            - len(covered_users)
            - len(covered_helpers),
        )

        candidates = sorted(
            (membership for membership in eligibility_index.memberships if membership.role == "staff"),
            key=lambda membership: membership.pk,
        )
        for membership in candidates:
            if remaining_count == 0:
                break
            if not eligibility_index.for_membership(membership).covers(requirement.start_time, requirement.end_time):
                continue
            user_id = membership.user_id
            if any(
                scheduled_user_id == user_id
                and start_time < requirement.end_time
                and end_time > requirement.start_time
                for scheduled_user_id, start_time, end_time in scheduled_shifts
            ):
                continue

            Shift.objects.create(
                user_id=user_id,
                membership=membership,
                store=store,
                work_date=requirement.work_date,
                start_time=requirement.start_time,
                end_time=requirement.end_time,
                is_generated=True,
            )
            scheduled_shifts.append(
                (user_id, requirement.start_time, requirement.end_time)
            )
            result["created_count"] += 1
            remaining_count -= 1

        result["shortfall_count"] += remaining_count

    return result
