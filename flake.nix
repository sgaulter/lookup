{
  description = "Selection lookup for Linux desktops";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pkgs.python3;
          mkLookup = backend: clipboardTools: python.pkgs.buildPythonApplication {
            pname = "lookup";
            version = "0.1.0";
            pyproject = true;
            src = self;
            build-system = [ python.pkgs.setuptools ];
            dependencies = [ python.pkgs.markdown-it-py ];
            nativeBuildInputs = [ pkgs.makeWrapper ];
            postFixup = ''
              wrapProgram "$out/bin/lookup" --prefix PATH : ${pkgs.lib.makeBinPath ([
                (pkgs.yad.override { withWebkit = true; })
                pkgs.libnotify
              ] ++ clipboardTools)} \
                --set LOOKUP_CLIPBOARD_BACKEND ${backend}
            '';
            checkPhase = ''
              runHook preCheck
              PYTHONPATH=src ${python.interpreter} -m unittest discover -s tests -v
              runHook postCheck
            '';
            pythonImportsCheck = [ "lookup.app" ];
            meta = {
              description = "Look up selected text in a desktop popup";
              license = pkgs.lib.licenses.mit;
              platforms = pkgs.lib.platforms.linux;
              mainProgram = "lookup";
            };
          };
        in {
          default = self.packages.${system}.lookup;
          lookup = mkLookup "auto" [ pkgs.wl-clipboard pkgs.xclip ];
          lookup-wayland = mkLookup "wayland" [ pkgs.wl-clipboard ];
          lookup-x11 = mkLookup "x11" [ pkgs.xclip ];
        });
      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.lookup}/bin/lookup";
          meta.description = "Look up selected text";
        };
      });
      checks = forAllSystems (system: {
        package = self.packages.${system}.lookup;
        wayland = self.packages.${system}.lookup-wayland;
        x11 = self.packages.${system}.lookup-x11;
      });
      devShells = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.mkShell {
            packages = [
              (pkgs.python3.withPackages (ps: [ ps.setuptools ps.markdown-it-py ]))
              pkgs.ruff pkgs.uv
            ];
            shellHook = ''export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"'';
          };
        });
    };
}
