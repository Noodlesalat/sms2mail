import sys
import dbus
import yaml
import smtplib
import email.utils
import logging
from email.mime.text import MIMEText
from datetime import datetime
from time import sleep
from email.utils import formataddr, parseaddr
import argparse

class MMSmsState(object):
    MM_SMS_STATE_UNKNOWN   = 0
    MM_SMS_STATE_STORED    = 1
    MM_SMS_STATE_RECEIVING = 2
    MM_SMS_STATE_RECEIVED  = 3
    MM_SMS_STATE_SENDING   = 4
    MM_SMS_STATE_SENT      = 5

class DBus(object):
    system_bus = None
    dbus_proxy = None

    @staticmethod
    def type_cast(val):
        if val is None:
            return None
        elif isinstance(val, (dbus.String, dbus.ObjectPath)):
            return str(val)
        elif isinstance(val, (dbus.Int32, dbus.UInt32)):
            return int(val)
        elif isinstance(val, dbus.Array):
            return [DBus.type_cast(e) for e in val]
        return val

    def __init__(self, *args, **kwargs):
        super(DBus, self).__init__(*args, **kwargs)
        self.system_bus = dbus.SystemBus()

    def setup_proxy_object(self, bus_name, object_path):
        self.dbus_proxy = self.system_bus.get_object(bus_name, object_path)

    def set_proxy_object(self, proxy_object):
        if isinstance(proxy_object, DBus):
            self.dbus_proxy = proxy_object.dbus_proxy
        elif isinstance(proxy_object, (dbus.Interface, dbus.proxies.ProxyObject)):
            self.dbus_proxy = proxy_object

    def get_proxy_object(self):
        return self.dbus_proxy

    def get_dbus_interface(self, interface):
        if self.dbus_proxy:
            return dbus.Interface(self.dbus_proxy, dbus_interface=interface)
        return None

    def get_objmanager_objects(self):
        if self.dbus_proxy:
            return self.get_dbus_interface('org.freedesktop.DBus.ObjectManager').GetManagedObjects()
        return {}

class DBusObject(DBus):
    obj_path = None

    def __init__(self, bus_name, path, *args, **kwargs):
        super(DBusObject, self).__init__(*args, **kwargs)
        self.obj_path = path
        self.setup_proxy_object(bus_name, path)

    def get_object_path(self):
        return self.obj_path

class ModemManagerObject(DBusObject):
    def __init__(self, obj_path, *args, **kwargs):
        super(ModemManagerObject, self).__init__(bus_name='org.freedesktop.ModemManager1', path=obj_path, *args, **kwargs)

    @staticmethod
    def object_path(obj, path = None):
        objbasepath = "/org/freedesktop/ModemManager1/%s/" % obj
        objid = None
        if isinstance(path, str) and objbasepath in path:
            objid = path.split('/')[-1]
        else:
            objid = path

        objpath = None
        try:
            objpath = "%s%d" % (objbasepath, int(objid))
        except (ValueError, TypeError):
            logging.error("Bad object ID provided: %s", objid)
        return objpath

class DBusInterface(DBus):
    name = None
    interface = None
    properties = None

    def __init__(self,  dbus_interface,  *args, **kwargs):
        super(DBusInterface, self).__init__(*args, **kwargs)
        self.name = dbus_interface
        self.interface = self.get_dbus_interface(dbus_interface)
        self.set_properties()

    def get_properties(self):
        try:
            if self.dbus_proxy:
                return self.dbus_proxy.GetAll(self.name, dbus_interface='org.freedesktop.DBus.Properties')
        except dbus.exceptions.DBusException as e:
            logging.error("Cannot get %s interface properties: %s", self.interface, e)
        return None

    def set_properties(self):
        self.properties = self.get_properties()

    def get_property(self, name):
        if self.properties and name in self.properties:
            return DBus.type_cast(self.properties[name])
        return None

    def setup_signal(self, name, handler):
        self.interface.connect_to_signal(name, handler)

class MMModem(DBusInterface, ModemManagerObject):
    def __init__(self, modem = None):
        path = ModemManagerObject.object_path('Modem', modem)
        super(MMModem, self).__init__(obj_path=path, dbus_interface='org.freedesktop.ModemManager1.Modem')

    def Manufacturer(self):
        return self.get_property('Manufacturer')

    def Model(self):
        return self.get_property('Model')

    def EquipmentIdentifier(self):
        return self.get_property('EquipmentIdentifier')

    def OwnNumbers(self):
        return self.get_property('OwnNumbers')

class MMModemSms(DBusInterface, ModemManagerObject):
    def __init__(self, sms):
        path = ModemManagerObject.object_path('SMS', sms)
        super(MMModemSms, self).__init__(obj_path=path, dbus_interface='org.freedesktop.ModemManager1.Sms')

    def Number(self):
        return self.get_property('Number')

    def Text(self):
        return self.get_property('Text')

    def State(self):
        return self.get_property('State')

    def Timestamp(self):
        return self.get_property('Timestamp')

    def get_datetime(self):
        stamp = self.Timestamp()
        if stamp:
            try:
                return datetime.fromisoformat(stamp)
            except ValueError:
                logging.warning("Invalid timestamp format: %s", stamp)
        return None

    def get_date(self):
        dt = self.get_datetime()
        if dt:
            return dt.strftime("%d.%m.%Y, %H:%M:%S Uhr")
        return "Datum nicht verfügbar"

class MMModemMessaging(DBusInterface):
    def __init__(self, modem):
        if not isinstance(modem, MMModem):
            modem = MMModem(modem)
        self.set_proxy_object(modem)
        super(MMModemMessaging, self).__init__(dbus_interface='org.freedesktop.ModemManager1.Modem.Messaging')

    def Messages(self):
        return self.get_property('Messages')

    def get_sms(self, sms = None, reverse=True):
        if sms is None:
            messages = list(
                filter(
                    lambda x: x.State() == MMSmsState.MM_SMS_STATE_RECEIVED,
                    map(lambda x: MMModemSms(x), self.Messages())
                )
            )
            messages.sort(key=lambda x: x.get_datetime() or datetime.min, reverse=reverse)
            return messages
        smspath = ModemManagerObject.object_path('SMS', sms)
        if smspath in self.Messages():
            return MMModemSms(smspath)
        return None

    def delete_sms(self, sms_path):
        self.interface.Delete(sms_path)

class ModemManager(ModemManagerObject):
    modems = None

    def __init__(self):
        super(ModemManager, self).__init__(obj_path='/org/freedesktop/ModemManager1')
        self.modems = self.get_modems_list()

    def get_modems_list(self):
        modems = []
        for p in self.get_objmanager_objects():
            if isinstance(p, dbus.ObjectPath):
                modems += [str(p)]
        return modems

    def get_modem(self, modem):
        mpath = ModemManagerObject.object_path('Modem', modem)
        if mpath in self.modems:
            return MMModem(mpath)
        return None

    def get_first(self):
        if self.modems:
            return self.get_modem(self.modems[0])
        return None

    def get_modem_by(self, name = 'OwnNumbers', value = None):
        if value is None:
            return self.get_first()
        elif name in ['Manufacturer', 'Model', 'EquipmentIdentifier',
                      'OwnNumbers', 'PrimaryPort', 'State']:
            modems = list(
                        filter(lambda x: value in x.get_property(name),
                            map(lambda x: MMModem(x), self.modems)))
            if len(modems) == 1:
                return modems[0]
            return modems
        return None

def send_email(smtp_server, smtp_port, smtp_user, smtp_password, from_addr, to_addrs, subject, body):
    from_name, from_email = parseaddr(from_addr)
    from_domain = from_email.split('@')[1]
    msg = MIMEText(body)
    msg['To'] = ', '.join([formataddr((name, email)) for name, email in to_addrs.items()])
    msg['From'] = formataddr((from_name, from_email))
    msg['Subject'] = subject
    msg['Message-ID'] = email.utils.make_msgid(domain = from_domain)
    msg['Date'] = email.utils.formatdate()

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(from_email, list(to_addrs.values()), msg.as_string())
        logging.info("Email sent successfully.")
    except Exception as e:
        logging.error("Failed to send email: %s", e)

def main(config_path, smtp_server, smtp_port, smtp_user, smtp_password, mail_from):
    with open(config_path, 'r') as file:
        config = yaml.safe_load(file)

    mail_to = config['smtp']['to']
    known_senders = config.get('known_senders', {})

    interval = config.get('interval', 60)
    delete_after_sending = config.get('delete_after_sending', False)
    continuous_mode = config.get('continuous_mode', False)

    def process_messages():
        mm = ModemManager()
        modem = mm.get_first()
        if modem:
            messaging = MMModemMessaging(modem)
            messages = messaging.get_sms()
            for msg in messages:
                sender_number = msg.Number()
                sender_name = known_senders.get(sender_number, sender_number)
                logging.info("SMS from %s: %s", sender_number, msg.Text())
                subject = f"New SMS from {sender_name}"
                body = f"From: {sender_name}\nDate: {msg.get_date()}\n\n{msg.Text()}"
                send_email(
                    smtp_server,
                    smtp_port,
                    smtp_user,
                    smtp_password,
                    mail_from,
                    mail_to,
                    subject,
                    body
                )
                if delete_after_sending:
                    try:
                        messaging.delete_sms(msg.get_object_path())
                        logging.info("Deleted SMS %s", msg.get_object_path())
                    except Exception as e:
                        logging.error("Failed to delete SMS %s: %s", msg.get_object_path(), e)

    if continuous_mode:
        while True:
            process_messages()
            sleep(interval)
    else:
        process_messages()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ModemManager SMS to Email forwarder")
    parser.add_argument('-c', '--config', type=str, required=True, help='Path to the configuration file')
    parser.add_argument('--smtp-server', type=str, required=True, help='SMTP server address')
    parser.add_argument('--smtp-port', type=int, required=True, help='SMTP server port')
    parser.add_argument('--smtp-user', type=str, required=True, help='SMTP server user')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--smtp-password', type=str, help='SMTP server password')
    group.add_argument('--smtp-password-file', type=str, help='Path to the file containing SMTP server password')
    parser.add_argument('--mail-from', type=str, required=True, help='Email address to use in the From field')

    args = parser.parse_args()

    # Set up logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    # Validate the arguments
    if args.smtp_port <= 0 or args.smtp_port > 65535:
        logging.error("Invalid SMTP port number.")
        sys.exit(1)

    if "@" not in args.mail_from:
        logging.error("Invalid email address for the From field.")
        sys.exit(1)

    # Get the SMTP password
    if args.smtp_password:
        smtp_password = args.smtp_password
    else:
        with open(args.smtp_password_file, 'r') as file:
            smtp_password = file.read().strip()

    main(
        args.config,
        args.smtp_server,
        args.smtp_port,
        args.smtp_user,
        smtp_password,
        args.mail_from
    )
