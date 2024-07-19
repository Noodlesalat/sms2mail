{ config, lib, pkgs, ... }:

with lib;

let
  sms2mail = pkgs.writers.writePython3Bin "sms2mail" {
    libraries = with pkgs.python3Packages; [ dbus-python pyyaml ];
    flakeIgnore = [ "E501" "E251" "E302" "E305" "E241" "E126" "E128" "E221" ];
  } (builtins.readFile ./sms2mail.py);

  configFormat = pkgs.formats.yaml {};
  configFile = configFormat.generate "config.yml" config.services.sms2mail.config;
in
{
  options = {
    services.sms2mail = {
      enable = mkOption {
        type = types.bool;
        default = false;
        description = "Enable the sms2mail service.";
      };

      config = mkOption {
        type = configFormat.type;
        default = {};

        description = '' '';
      };

      smtpServer = mkOption {
        type = types.str;
        default = "smtp.google.de";
        description = "SMTP server address.";
      };

      smtpPort = mkOption {
        type = types.int;
        default = 587;
        description = "SMTP server port.";
      };

      smtpUser = mkOption {
        type = types.str;
        default = "root@gmail.com";
        description = "SMTP user email address.";
      };

      smtpPasswordFile = mkOption {
        type = types.str;
        default = "/root/passwd";
        description = "Path to the file containing the SMTP password.";
      };

      mailFrom = mkOption {
        type = types.str;
        default = "Marc Zuckerberg <root@gmail.com>";
        description = "Email address used in the 'From' field.";
      };
    };
  };

  config = mkIf config.services.sms2mail.enable {
    environment.systemPackages = [ sms2mail ];

    systemd.services.sms2mail = {
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      description = "sms2mail Daemon.";
      serviceConfig.ExecStart = ''
        ${sms2mail}/bin/sms2mail \
          --config ${configFile} \
          --smtp-server ${config.services.sms2mail.smtpServer} \
          --smtp-port ${toString config.services.sms2mail.smtpPort} \
          --smtp-user ${config.services.sms2mail.smtpUser} \
          --smtp-password-file ${config.services.sms2mail.smtpPasswordFile} \
          --mail-from "${config.services.sms2mail.mailFrom}"
      '';
    };
  };
}
