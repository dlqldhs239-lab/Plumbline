from django.db import migrations, models


class Migration(migrations.Migration):
    """Room for an Ed25519 signature: 64 bytes, 128 hex characters."""

    dependencies = [("records", "0001_initial")]

    operations = [
        migrations.AlterField(model_name="record", name="signature", field=models.CharField(max_length=128)),
    ]
