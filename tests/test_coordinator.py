from __future__ import annotations

import time

from pikvm_pkg import coordinator as co


def msg(c, t, e):
    c._process_ws_message({"event_type": t, "event": e})


def test_streamer_partial_merge(coord):
    msg(coord, "streamer", {
        "features": {"quality": True}, "limits": {"desired_fps": {"min": 1, "max": 30}},
        "params": {"quality": 80},
        "streamer": {"source": {"online": True, "resolution": {"width": 1280, "height": 720},
                                "captured_fps": 29}, "stream": {"clients": 2}},
    })
    s = coord._state["streamer"]
    assert s["resolution"] == "1280x720" and s["online"] and s["captured_fps"] == 29
    assert s["clients"] == 2 and s["limits"] == {"desired_fps": {"min": 1, "max": 30}}
    n = len(coord.pubs)
    msg(coord, "streamer", {"params": {"desired_fps": 25}})
    s = coord._state["streamer"]
    assert s["params"] == {"quality": 80, "desired_fps": 25}
    assert s["features"] == {"quality": True} and s["resolution"] == "1280x720"
    assert len(coord.pubs) == n + 1


def test_streamer_null_offline(coord):
    msg(coord, "streamer", {"params": {"quality": 50},
                            "streamer": {"source": {"online": True, "resolution": {"width": 1, "height": 2},
                                                    "captured_fps": 5}, "stream": {"clients": 1}}})
    msg(coord, "streamer", {"streamer": None})
    s = coord._state["streamer"]
    assert s["online"] is False and s["resolution"] is None
    assert s["captured_fps"] is None and s["clients"] is None
    assert s["params"] == {"quality": 50}


def test_identical_repeat_returns_false(coord):
    ev = {"params": {"desired_fps": 25}}
    assert coord._process_streamer_event(ev) is True
    assert coord._process_streamer_event(ev) is False
    n = len(coord.pubs)
    msg(coord, "streamer", ev)
    assert len(coord.pubs) == n


def test_no_aliasing(coord):
    msg(coord, "streamer", {"params": {"quality": 80}})
    prev = coord.pubs[-1]["streamer"]
    prev_params = prev["params"]
    msg(coord, "streamer_state", {"params": {"quality": 50}})
    assert prev["params"] is prev_params and prev_params["quality"] == 80
    assert coord.pubs[-1]["streamer"] is not prev
    assert coord._state["streamer"]["params"]["quality"] == 50

    msg(coord, "atx", {"leds": {"power": True, "hdd": False}})
    old_leds = coord.pubs[-1]["atx"]["leds"]
    msg(coord, "atx", {"leds": {"hdd": True}})
    assert old_leds == {"power": True, "hdd": False}
    assert coord._state["atx"]["leds"] == {"power": True, "hdd": True}

    msg(coord, "gpio", {"inputs": {"a": {"online": True, "state": False}}})
    og = coord.pubs[-1]["gpio"]["inputs"]["a"]
    msg(coord, "gpio", {"inputs": {"a": {"state": True}}})
    assert og["state"] is False
    assert coord._state["gpio"]["inputs"]["a"] == {"online": True, "state": True}


def test_info_events(coord):
    msg(coord, "info_system_state", {"kvmd": {"version": "4.1"}})
    msg(coord, "info_meta_state", {"server": {"host": "pikvm.lan"}})
    msg(coord, "info_hw_state", {
        "platform": {"model": "v3", "base": "rpi4", "serial": "abc"},
        "health": {"temp": {"cpu": 50.0},
                   "throttling": {"parsed_flags": {"undervoltage": {"now": True}}}},
    })
    assert coord._state["info"] == {"kvmd_version": "4.1", "hostname": "pikvm.lan",
                                    "model": "v3", "base": "rpi4", "serial": "abc"}
    assert coord._state["system"]["cpu_temp"] == 50.0
    assert coord._state["system"]["throttling"]["undervoltage"] is True
    n = len(coord.pubs)
    msg(coord, "info_hw_state", {"platform": {"model": "v3"}, "health": {"temp": {"cpu": 50.0}}})
    msg(coord, "info_system_state", {"kvmd": {"version": "4.1"}})
    assert len(coord.pubs) == n


def test_hid_outputs(coord):
    msg(coord, "hid", {
        "keyboard": {"online": True, "outputs": {"available": ["usb"], "active": "usb"}},
        "mouse": {"online": False, "outputs": {"available": ["usb", "usb_rel"], "active": "usb"}},
        "jiggler": {"enabled": True},
    })
    h = coord._state["hid"]
    assert h["keyboard_online"] is True and h["mouse_online"] is False
    assert h["keyboard_outputs"] == {"available": ["usb"], "active": "usb"}
    assert h["mouse_outputs"] == {"available": ["usb", "usb_rel"], "active": "usb"}
    assert h["jiggler"] is True


def test_push_partial(coord):
    coord.push_partial("hid", jiggler=True)
    n = len(coord.pubs)
    coord.push_partial("hid", jiggler=True)
    assert len(coord.pubs) == n
    coord.push_partial("hid", jiggler=False)
    assert len(coord.pubs) == n + 1


def test_ws_available_grace(coord):
    assert coord.ws_available
    coord._ws_disconnected_since = time.monotonic() - 100
    assert not coord.ws_available
    coord._ws_disconnected_since = time.monotonic() - 5
    assert coord.ws_available
    coord._state["ws_connected"] = True
    coord._ws_disconnected_since = time.monotonic() - 100
    assert coord.ws_available


def test_atx_msd_merge(coord):
    msg(coord, "atx", {"enabled": True, "busy": False, "leds": {"power": True, "hdd": False}})
    msg(coord, "atx", {"busy": True})
    a = coord._state["atx"]
    assert a["enabled"] is True and a["busy"] is True and a["leds"]["power"] is True
    msg(coord, "msd", {"enabled": True, "drive": {"connected": False, "image": "a.iso"},
                       "storage": {"images": {"a.iso": {}, "b.iso": {}}}})
    msg(coord, "msd", {"drive": {"connected": True}})
    m = coord._state["msd"]
    assert m["connected"] is True and m["image"] == "a.iso" and m["images"] == ["a.iso", "b.iso"]


def test_gpio_labels_and_model(coord):
    coord._process_gpio_full({
        "model": {
            "scheme": {"inputs": {"ch1_led": {}}, "outputs": {"ch1_button": {"pulse": {}}}},
            "view": {"table": [[{"type": "label", "text": "Port 1"},
                                {"type": "input", "channel": "ch1_led"},
                                {"type": "output", "channel": "ch1_button"}], "junk"]},
        },
        "state": {"inputs": {"ch1_led": {"online": True, "state": False}}, "outputs": {}},
    })
    assert coord._state["gpio_labels"] == {"ch1_led": "Port 1", "ch1_button": "Port 1"}
    assert "ch1_led" in coord._state["gpio_model"]["inputs"]
    msg(coord, "gpio", {"inputs": {"ch1_led": {"state": True}}})
    assert coord._state["gpio"]["inputs"]["ch1_led"] == {"online": True, "state": True}
