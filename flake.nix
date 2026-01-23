{
  description = "NixOS flake for snowflake-document-agent";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux"; # or aarch64-darwin
      pkgs = nixpkgs.legacyPackages.${system};
      venvDir = "./.venv";
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        inherit venvDir;
        
        # Ensure ruff and snowflake-cli are installed
        packages = with pkgs; [
          ruff
          (snowflake-cli.overridePythonAttrs (oldAttrs: { doCheck = false; }))
        ];

        # Packages available in the shell
        buildInputs = with pkgs; [
          python313
          python313Packages.venvShellHook
          uv
        ];

        # 1. Tell uv not to download Python; use the one from buildInputs
        # 2. Force the venv to use the specific Python binary
        env = {
          UV_PYTHON_DOWNLOADS = "never";
          UV_PYTHON = "${pkgs.python313}/bin/python";
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib ];
        };


        # When the shell starts:
        # Unset PYTHONPATH so that snowflake.cli doesn't pollute the Python namespace
        # Remove ruff to use the packaged version instead
        postShellHook = ''
          unset PYTHONPATH
          uv sync
          rm -f ${venvDir}/bin/ruff
        '';
      };
    };
}

