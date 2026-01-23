{
  description = "NixOS flake for snowflake-document-agent";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux"; # or aarch64-darwin
      pkgs = nixpkgs.legacyPackages.${system};
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        venvDir = "./.venv";
        
        # Ensure snowflake-cli is installed
        packages = with pkgs; [
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
        };


        # Run when the shell starts
        postShellHook = ''
          unset PYTHONPATH
          uv sync
        '';
      };
    };
}

