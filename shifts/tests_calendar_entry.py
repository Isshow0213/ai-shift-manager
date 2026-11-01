"""Daily wishes entered from the employee calendar, with scoped suggestions."""
from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Company, Store, StoreMembership
from .models import Availability, DayOffRequest, Requirement, ShiftSubmission, StoreOperatingHours, StoreSubmissionDeadline


class CalendarEntryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="希望入力テスト")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.user = get_user_model().objects.create_user(username="calendar_entry_staff")
        cls.member = StoreMembership.objects.create(user=cls.user, store=cls.store, role="staff")
        cls.other_member = StoreMembership.objects.create(user=cls.user, store=cls.other_store, role="staff")
        cls.colleague = get_user_model().objects.create_user(username="calendar_entry_other")
        cls.colleague_member = StoreMembership.objects.create(user=cls.colleague, store=cls.store, role="staff")
        cls.work_date = date(2030, 12, 12)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = (f"{reverse('availability_list')}?year=2030&month=12"
                    f"&date={self.work_date.isoformat()}&store={self.store.pk}")
        self.data = {"work_date": self.work_date.isoformat(), "start_time": "09:00", "end_time": "17:00"}

    def history(self, *, user=None, member=None, day=None, start=9, end=17):
        member = member or self.member
        return Availability.objects.create(user=user or member.user, membership=member, store=member.store,
            work_date=day or self.work_date-timedelta(days=1), start_time=time(start), end_time=time(end))

    def test_selected_day_opens_unbound_form_without_saving(self):
        before = list(Availability.objects.values())
        response = self.client.get(self.url + "&edit=1")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['calendar_form_open'])
        form = response.context['calendar_form']
        self.assertFalse(form.is_bound)
        self.assertEqual(form['work_date'].value(), self.work_date)
        self.assertTrue(form.fields['work_date'].widget.attrs['readonly'])
        self.assertEqual(list(Availability.objects.values()), before)
        response = self.client.get(self.url)
        self.assertFalse(response.context['calendar_form_open'])

    def test_history_deduplicates_before_limiting_and_stays_private_to_user_and_store(self):
        for offset in range(1,7):
            self.history(day=self.work_date-timedelta(days=offset))
        for offset, start in enumerate((8,10,11,12,13),start=8):
            self.history(day=self.work_date-timedelta(days=offset),start=start,end=18)
        self.history(member=self.other_member,start=6,end=20)
        self.history(member=self.colleague_member,start=7,end=21)
        self.history(day=self.work_date,start=5,end=19)
        self.history(day=self.work_date+timedelta(days=1),start=4,end=20)
        self.history(start=20,end=19)
        response = self.client.get(self.url + "&edit=1")
        suggestions = response.context['previous_availabilities_unique']
        self.assertEqual([(s.start_time,s.end_time) for s in suggestions], [
            (time(9),time(17)), (time(8),time(18)), (time(10),time(18)),
            (time(11),time(18)), (time(12),time(18)),
        ])
        self.assertEqual(suggestions[0].work_date,self.work_date-timedelta(days=1))
        standalone = self.client.get(reverse('availability_create'), {'date':self.work_date.isoformat(),'store':self.store.pk})
        self.assertEqual([(s.start_time,s.end_time) for s in standalone.context['previous_availabilities_unique']],
                         [(s.start_time,s.end_time) for s in suggestions])

    def test_manager_times_and_full_day_hours_use_only_selected_store_and_date(self):
        requirement = Requirement.objects.create(store=self.store,work_date=self.work_date,
            start_time=time(8),end_time=time(18),required_staff_count=2,memo='早番希望')
        Requirement.objects.create(store=self.other_store,work_date=self.work_date,
            start_time=time(6),end_time=time(21),required_staff_count=1)
        Requirement.objects.create(store=self.store,work_date=self.work_date+timedelta(days=1),
            start_time=time(7),end_time=time(20),required_staff_count=1)
        StoreOperatingHours.objects.create(store=self.store,start_time=time(8),end_time=time(19))
        StoreOperatingHours.objects.create(store=self.other_store,start_time=time(6),end_time=time(23))
        response = self.client.get(self.url + '&edit=1')
        self.assertEqual(list(response.context['requirements_for_date']),[requirement])
        self.assertTrue(response.context['has_full_day_hours'])
        self.assertEqual(response.context['operating_hours'].start_time,time(8))

    def test_calendar_save_uses_current_membership_and_returns_to_calendar_anchor(self):
        submission = ShiftSubmission.objects.create(membership=self.member,target_month=self.work_date.replace(day=1),
            submission_mode='availability',is_submitted=True,submitted_at=timezone.now())
        response = self.client.post(self.url,{**self.data,'user':self.colleague.pk,
            'membership':self.other_member.pk,'store':self.other_store.pk})
        self.assertRedirects(response,self.url+'#availability-calendar',fetch_redirect_response=False)
        saved = Availability.objects.get()
        self.assertEqual(saved.user,self.user)
        self.assertEqual(saved.membership,self.member)
        self.assertEqual(saved.store,self.store)
        self.assertEqual(saved.submission,submission)
        submission.refresh_from_db()
        self.assertFalse(submission.is_submitted)

    def test_calendar_post_rejects_a_date_other_than_selected_day(self):
        response = self.client.post(self.url,{**self.data,'work_date':'2030-12-13'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['calendar_form_open'])
        self.assertIn('work_date',response.context['calendar_form'].errors)
        self.assertFalse(Availability.objects.exists())

    def test_invalid_input_and_overlap_reopen_form_and_keep_entered_values(self):
        self.history(day=self.work_date,start=9,end=12)
        for data in ({**self.data,'start_time':'18:00'}, self.data):
            with self.subTest(data=data):
                before=list(Availability.objects.values())
                response=self.client.post(self.url,data)
                self.assertEqual(response.status_code,200)
                self.assertTrue(response.context['calendar_form_open'])
                self.assertTrue(response.context['calendar_form'].errors)
                self.assertEqual(response.context['calendar_form']['start_time'].value(),data['start_time'])
                self.assertEqual(list(Availability.objects.values()),before)

    def test_day_off_and_all_day_settings_keep_exclusivity_and_draft_state(self):
        self.member.submission_mode='day_off'
        self.member.save(update_fields=['submission_mode'])
        self.history(day=self.work_date)
        data={**self.data,'day_status':'day_off'}
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,302)
        self.assertFalse(Availability.objects.filter(user=self.user,work_date=self.work_date).exists())
        self.assertTrue(DayOffRequest.objects.filter(submission__membership=self.member,work_date=self.work_date).exists())
        response=self.client.post(self.url,{**data,'day_status':'all_day'})
        self.assertEqual(response.status_code,302)
        self.assertFalse(DayOffRequest.objects.exists())
        self.assertFalse(Availability.objects.exists())
        self.assertFalse(ShiftSubmission.objects.get().is_submitted)

    def test_read_only_membership_cannot_open_or_post_calendar_entry(self):
        self.member.can_submit_availability=False
        self.member.save(update_fields=['can_submit_availability'])
        response=self.client.get(self.url+'&edit=1')
        self.assertFalse(response.context['calendar_form_open'])
        self.assertIsNone(response.context['calendar_form'])
        response=self.client.post(self.url,self.data)
        self.assertEqual(response.status_code,403)
        self.assertFalse(Availability.objects.exists())

    def test_closed_month_cannot_open_or_save_calendar_entry(self):
        StoreSubmissionDeadline.objects.create(store=self.store,mode='monthly',monthly_deadline_day=15)
        url=self.url.replace('2030','2020')
        response=self.client.get(url+'&edit=1')
        self.assertFalse(response.context['calendar_form_open'])
        response=self.client.post(url,{**self.data,'work_date':'2020-12-12'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['calendar_form_open'])
        self.assertTrue(response.context['calendar_form'].errors)
        self.assertFalse(Availability.objects.exists())

    def test_foreign_store_and_anonymous_submission_are_rejected(self):
        foreign_store=Store.objects.create(company=self.store.company,name='所属外')
        response=self.client.post(self.url.replace(f'store={self.store.pk}',f'store={foreign_store.pk}'),self.data)
        self.assertEqual(response.status_code,404)
        self.client.logout()
        response=self.client.post(self.url,self.data)
        self.assertEqual(response.status_code,302)
        self.assertIn(reverse('login'),response.url)
        self.assertFalse(Availability.objects.exists())
