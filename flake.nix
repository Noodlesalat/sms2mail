{
  inputs = {
    nixpkgs.url = "github:Nixos/nixpkgs/nixos-24.05";
  };

  outputs = { ... }: rec {
    nixosModules = rec {
      sms2mail = import ./default.nix;
      default = sms2mail;
    };
    nixosModule = nixosModules.default;
  };
}
