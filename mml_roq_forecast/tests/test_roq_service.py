"""Tests for ROQService."""


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

class _FakeNullService:
    """Mimics NullService: all calls return None."""
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _FakeFreightService:
    """Mimics a FreightService with a single known booking."""
    def __init__(self, known_booking_id, transit_days):
        self._known = {known_booking_id: transit_days}

    def get_booking_lead_time(self, booking_id):
        return self._known.get(booking_id)


class _Namespace:
    """Simple attribute bag."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeRegistry:
    """Minimal env['mml.registry'] stand-in."""
    def __init__(self, service_obj):
        self._svc = service_obj

    def service(self, _name):
        return self._svc


class _FakeEnv:
    def __init__(self, registry_svc, booking=None):
        self._registry = _FakeRegistry(registry_svc)
        self._booking = booking

    def __getitem__(self, model):
        if model == 'mml.registry':
            return self._registry
        if model == 'freight.booking':
            return self
        raise KeyError(model)

    def browse(self, booking_id):
        return self._booking


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------

def test_roq_service_importable():
    from mml_roq_forecast.services.roq_service import ROQService
    assert ROQService is not None


def test_roq_service_has_on_freight_booking_confirmed():
    from mml_roq_forecast.services.roq_service import ROQService
    assert callable(getattr(ROQService, 'on_freight_booking_confirmed', None))


def test_roq_service_constructor_stores_env():
    from mml_roq_forecast.services.roq_service import ROQService
    svc = ROQService(_FakeEnv(_FakeNullService()))
    assert svc.env is not None


# ---------------------------------------------------------------------------
# Behavioural tests
# ---------------------------------------------------------------------------

def test_exits_early_when_res_id_is_none():
    """Handler must return immediately when event has no res_id."""
    from mml_roq_forecast.services.roq_service import ROQService

    update_called = []

    class TrackingPartner:
        def action_update_lead_time_stats(self):
            update_called.append(True)

    svc = ROQService(_FakeEnv(_FakeNullService()))
    event = _Namespace(res_id=None)
    svc.on_freight_booking_confirmed(event)
    assert update_called == [], "action_update_lead_time_stats must not be called when res_id is None"


def test_exits_early_when_freight_service_returns_none():
    """Handler must return when get_booking_lead_time returns None (booking missing or mml_freight uninstalled)."""
    from mml_roq_forecast.services.roq_service import ROQService

    update_called = []

    class TrackingPartner:
        def action_update_lead_time_stats(self):
            update_called.append(True)

    # NullService returns None for all calls
    env = _FakeEnv(_FakeNullService())
    svc = ROQService(env)
    event = _Namespace(res_id=42)
    svc.on_freight_booking_confirmed(event)
    assert update_called == [], "action_update_lead_time_stats must not be called when transit_days is None"


def test_exits_early_when_booking_has_no_purchase_order():
    """Handler must not call action_update_lead_time_stats when booking has no PO."""
    from mml_roq_forecast.services.roq_service import ROQService

    update_called = []

    class FakePartner:
        def action_update_lead_time_stats(self):
            update_called.append(True)

    booking = _Namespace(purchase_order_id=None)
    env = _FakeEnv(_FakeFreightService(known_booking_id=42, transit_days=14.0), booking=booking)
    svc = ROQService(env)
    event = _Namespace(res_id=42)
    svc.on_freight_booking_confirmed(event)
    assert update_called == [], "action_update_lead_time_stats must not be called when booking has no PO"


def test_calls_action_update_lead_time_stats_on_happy_path():
    """Handler must call action_update_lead_time_stats on the booking's supplier partner."""
    from mml_roq_forecast.services.roq_service import ROQService

    update_called = []

    class FakePartner:
        def action_update_lead_time_stats(self):
            update_called.append(True)

    fake_po = _Namespace(partner_id=FakePartner())
    booking = _Namespace(purchase_order_id=fake_po)
    env = _FakeEnv(_FakeFreightService(known_booking_id=99, transit_days=21.0), booking=booking)
    svc = ROQService(env)
    event = _Namespace(res_id=99)
    svc.on_freight_booking_confirmed(event)
    assert update_called == [True], "action_update_lead_time_stats must be called on the supplier partner"
