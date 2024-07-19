# Django Multiverse

Django Multiverse: A Multi-Tenancy reusable Django app that provides a robust and flexible way to manage tenants in your Django project. This package is distinguished from the popular django-tenants because it separates the tenants into different databases. (Currently supports SQLite and PostgreSQL).

## Features

- Easy integration with existing Django projects
- Support for multiple Django versions (4.0, 4.1, 4.2, 5.0)
- Compatible with Python 3.10, 3.11, and 3.12
- Tenant separation in different databases
- Middleware for tenant-aware requests
- Utilities for tenant management
- Support for Django Rest Framework (DRF) and Django Q2
- MIT License

## Installation

To install Django Multiverse, you can use pip:

```bash
pip install django-multiverse
```

Alternatively, you can add it to your `pyproject.toml` file:

```toml
[tool.poetry.dependencies]
django-multiverse = "^1.0.6"
```

## Usage

To use Django Multiverse in your project, follow these steps:

1. Add `multiverse` to your `INSTALLED_APPS` in `settings.py`:

    ```python
    INSTALLED_APPS = [
        ...
        'multiverse',
        ...
    ]
    ```

2. Configure your middleware to include `TenantMiddleware`:

    ```python
    MIDDLEWARE = [
        ...
        'django_multiverse.middleware.TenantMiddleware',
        ...
    ]
    ```

3. Define your tenant model and configure it in your settings:

    ```python
    TENANT_MODEL = 'yourapp.Tenant'
    ```

4. Use the provided mixins and decorators to manage tenant-specific views and models.

5. To create a tenant, use the management command:

    ```bash
    python manage.py create_tenant <subdomain> --database-name <database_name> --create-database --migrate
    ```

6. To destroy a tenant, use the management command:

    ```bash
    python manage.py destroy_tenant <lookup> --drop-database
    ```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request or open an issue on our [GitHub repository](https://github.com/dmp593/django-multiverse).


### Draft Mode

Although this library is being used in production in some of my projects, this documentation is currently a draft. If you are interested in exploring further, you are welcome to open a pull request with your questions, and I will gladly assist you if I can.


## License

This project is licensed under the MIT License.

---

Thank you for reading!

If you like this library, you can support me by [buying me a coffee](https://www.buymeacoffee.com/dmp593).
