import calendar

from django.utils import timezone
from django.utils.dateparse import parse_date


def get_calendar_context(request, markers=None):
    today = timezone.localdate()

    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))

    selected_date_text = request.GET.get("date")
    selected_date = parse_date(selected_date_text) if selected_date_text else today

    if selected_date is None:
        selected_date = today

    calendar_obj = calendar.Calendar(firstweekday=0)
    month_weeks = calendar_obj.monthdatescalendar(year, month)

    if month == 1:
        prev_year = year - 1
        prev_month = 12
    else:
        prev_year = year
        prev_month = month - 1

    if month == 12:
        next_year = year + 1
        next_month = 1
    else:
        next_year = year
        next_month = month + 1

    markers = markers or {}

    calendar_weeks = []

    for week in month_weeks:
        week_days = []

        for day in week:
            day_data = {
                "date": day,
                "day": day.day,
                "is_current_month": day.month == month,
                "is_today": day == today,
                "is_selected": day == selected_date,
                "url": f"?year={year}&month={month}&date={day.isoformat()}",
            }

            for marker_name, marker_dates in markers.items():
                day_data[marker_name] = day in marker_dates

            week_days.append(day_data)

        calendar_weeks.append(week_days)

    return {
        "today": today,
        "year": year,
        "month": month,
        "selected_date": selected_date,
        "calendar_weeks": calendar_weeks,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
    }