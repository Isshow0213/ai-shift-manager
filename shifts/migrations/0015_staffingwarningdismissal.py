from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("shifts", "0014_add_shift_display_colors"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffingWarningDismissal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("work_date", models.DateField(verbose_name="対象日")),
                ("staffing_signature", models.CharField(max_length=64, verbose_name="確認した配置内容")),
                ("dismissed_at", models.DateTimeField(auto_now=True, verbose_name="確認日時")),
                ("dismissed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="staffing_warning_dismissals", to=settings.AUTH_USER_MODEL)),
                ("store", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="staffing_warning_dismissals", to="accounts.store")),
            ],
            options={
                "verbose_name": "人数不足警告の解除",
                "verbose_name_plural": "人数不足警告の解除",
                "constraints": [models.UniqueConstraint(fields=("store", "work_date"), name="unique_store_staffing_warning_dismissal")],
            },
        ),
    ]
