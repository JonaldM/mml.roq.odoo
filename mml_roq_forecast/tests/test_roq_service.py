"""Pure-Python structural tests for ROQService."""
import pytest


def test_roq_service_importable():
    from mml_roq_forecast.services.roq_service import ROQService
    assert ROQService is not None


def test_roq_service_has_on_freight_booking_confirmed():
    from mml_roq_forecast.services.roq_service import ROQService
    assert callable(getattr(ROQService, 'on_freight_booking_confirmed', None))


def test_roq_service_constructor_stores_env():
    from mml_roq_forecast.services.roq_service import ROQService

    class FakeEnv:
        pass

    svc = ROQService(FakeEnv())
    assert svc.env is not None
