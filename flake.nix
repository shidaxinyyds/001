{
description = "Flutter 3.13.x";
inputs = {
  nixpkgs.url = "github:NixOS/nixpkgs/23.11";
  flake-utils.url = "github:numtide/flake-utils";
  nixpkgs-python.url = "github:NixOS/nixpkgs/6f05cfdb1e78d36c0337516df674560e4b51c79b";
};
outputs = { self, nixpkgs, flake-utils, nixpkgs-python }:
  flake-utils.lib.eachDefaultSystem (system:
    let
      pkgs = import nixpkgs {
        inherit system;
        config = {
          android_sdk.accept_license = true;
          allowUnfree = true;
        };
      };
      pkgs-python = import nixpkgs-python {
        inherit system;
      };
      buildToolsVersion = "34.0.0";
      androidComposition = pkgs.androidenv.composeAndroidPackages {
        buildToolsVersions = [ buildToolsVersion "30.0.3" ];
        platformVersions = [ "33" "34" "32" "31" "30" "29" ];
        # abiVersions = [ "armeabi-v7a" "arm64-v8a" ];
      };
      androidSdk = androidComposition.androidsdk;
    in
    {
      devShell =
        with pkgs; mkShell rec {
          ANDROID_SDK_ROOT = "${androidSdk}/libexec/android-sdk";
          buildInputs = [
            flutter
            androidSdk
            jdk17
            android-studio

            (let
              python-packages = ps: with ps; [
                opencv4
                numpy
                pillow
                (buildPythonPackage rec {
                pname = "mahjong";
                  version = "1.2.1";
                  src = fetchPypi {
                    inherit pname version;
                    sha256 = "r77y9f6mVrc+rLY5YXbR6AzQYSPCTb6iffhxAOhQIJc=";
                  };
                  doCheck = false;
                  propagatedBuildInputs = [];
                })
              ];
            in pkgs-python.python38.withPackages python-packages)
          ];
        };
    });
}

