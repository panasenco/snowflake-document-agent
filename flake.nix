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

        # Install ruff separately to avoid linked executable issues
        packages = with pkgs; [
          ruff
        ];
        
        # Packages available in the shell
        buildInputs = with pkgs; [
          python313
          python313Packages.venvShellHook
          uv
        ];

        env = {
          # Tell uv not to download Python; use the one from buildInputs
          UV_PYTHON_DOWNLOADS = "never";
          UV_PYTHON = "${pkgs.python313}/bin/python";
          # Allow Python packages to access C/C++ shared libraries
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib ];
        };


        # Install uv packages and remove ruff to use the packaged version instead
        postShellHook = ''
          uv sync
          rm -f ${venvDir}/bin/ruff
        '';
      };
    };
}

