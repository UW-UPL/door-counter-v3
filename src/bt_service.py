import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib
from device_manager import add_pending_device

AGENT_INTERFACE = "org.bluez.Agent1"
AGENT_PATH = "/upl/agent"


class UPLAgent(dbus.service.Object):
    def __init__(self, bus, path):
        super().__init__(bus, path)
        self.bus = bus
        self.pending_devices = set()

    def _extract_mac(self, device_path):
        # /org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF -> AA:BB:CC:DD:EE:FF
        return device_path.split("/")[-1].replace("dev_", "").replace("_", ":")

    def _disconnect(self, device_path):
        try:
            device = self.bus.get_object("org.bluez", device_path)
            device.Disconnect(dbus_interface="org.bluez.Device1")
        except Exception as e:
            print(f"Error disconnecting {device_path}: {e}")

    def _accept_and_watch(self, device):
        if device in self.pending_devices:
            return

        self.pending_devices.add(device)

        def on_properties_changed(interface, changed, _):
            if interface == "org.bluez.Device1" and changed.get("Paired"):
                def disconnect_and_cleanup():
                    self._disconnect(device)
                    self.pending_devices.discard(device)
                    return False
                # Wait for key exchange to complete
                GLib.timeout_add_seconds(10, disconnect_and_cleanup)

        self.bus.add_signal_receiver(
            on_properties_changed,
            signal_name="PropertiesChanged",
            dbus_interface="org.freedesktop.DBus.Properties",
            path=device
        )

    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, _):
        # Reject all service requests - we only want the Identity MAC
        # self._disconnect(device)
        raise dbus.DBusException("org.bluez.Error.Rejected", "Service access denied")

    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def RequestConfirmation(self, device, passkey):
        mac = self._extract_mac(device)
        add_pending_device(mac, str(passkey))
        self._accept_and_watch(device)

        print(f"\nNEW DEVICE PAIRED:")
        print(f"  MAC: {mac}")
        print(f"  Passkey: {passkey}\n")

        return  # Accept pairing


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    adapter = bus.get_object("org.bluez", "/org/bluez/hci0")
    adapter_props = dbus.Interface(adapter, "org.freedesktop.DBus.Properties")
    adapter_iface = dbus.Interface(adapter, "org.bluez.Adapter1")

    print("Setting up Bluetooth adapter...")
    adapter_props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
    adapter_props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(True))
    adapter_props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(True))
    print("Adapter ready - discoverable and pairable")

    agent = UPLAgent(bus, AGENT_PATH)
    agent_manager = dbus.Interface(
        bus.get_object("org.bluez", "/org/bluez"),
        "org.bluez.AgentManager1"
    )
    agent_manager.RegisterAgent(AGENT_PATH, "DisplayYesNo")
    agent_manager.RequestDefaultAgent(AGENT_PATH)

    adapter_iface.StartDiscovery()
    print("Discovery started - waiting for devices to pair...\n")

    main_loop = GLib.MainLoop()
    try:
        main_loop.run()
    except KeyboardInterrupt:
        print("\nStopping Bluetooth service...")
    finally:
        try:
            adapter_iface.StopDiscovery()
        except:
            pass

        adapter_props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(False))

        try:
            agent_manager.UnregisterAgent(AGENT_PATH)
        except:
            pass

        print("Cleanup complete")


if __name__ == "__main__":
    main()
