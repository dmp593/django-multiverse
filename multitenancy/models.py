from mywise.models import BaseModel, models


class BaseTenant(BaseModel):
    subdomain = models.CharField(
        max_length=50,
        null=False,
        unique=True
    )

    database_name = models.CharField(
        max_length=50,
        null=False,
        unique=True
    )

    class Meta:
        abstract = True


class Tenant(BaseTenant):
    class Meta(BaseTenant.Meta):
        swappable = "TENANT_MODEL"
