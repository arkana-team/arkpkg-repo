#!/usr/bin/env python3
"""Build checksum-pinned upstream CLI tools as arkpkg packages."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

import lz4.frame

PACKAGES = {
    "neofetch": {
        "version": "7.1.0",
        "arch": "any",
        "url": "https://github.com/dylanaraps/neofetch",
        "archive": "https://codeload.github.com/dylanaraps/neofetch/tar.gz/refs/tags/7.1.0",
        "archive_sha256": "58a95e6b714e41efc804eca389a223309169b2def35e57fa934482a6b47c27e7",
        "root": "neofetch-7.1.0",
        "license_source": "LICENSE.md",
        "license_path": "usr/share/doc/neofetch/copyright",
        "files": {
            "neofetch": "usr/bin/neofetch",
            "neofetch.1": "usr/share/man/man1/neofetch.1",
        },
        "description": "System information and distribution logo tool",
        "license": "MIT",
    },
    "pfetch": {
        "version": "0.6.0",
        "arch": "any",
        "url": "https://github.com/dylanaraps/pfetch",
        "archive": "https://codeload.github.com/dylanaraps/pfetch/tar.gz/refs/tags/0.6.0",
        "archive_sha256": "d1f611e61c1f8ae55bd14f8f6054d06fcb9a2d973095367c1626842db66b3182",
        "root": "pfetch-0.6.0",
        "license_source": "LICENSE.md",
        "license_path": "usr/share/doc/pfetch/copyright",
        "files": {"pfetch": "usr/bin/pfetch"},
        "description": "Small POSIX shell system information tool",
        "license": "MIT",
    },
    "nitch": {
        "version": "0.1.6",
        "arch": "x86_64",
        "url": "https://github.com/ssleert/nitch",
        "archive": "https://codeload.github.com/ssleert/nitch/tar.gz/refs/tags/0.1.6",
        "archive_sha256": "118654cf34b9b5d0f0babccad623360438b6c1e9d619770f590d192de2183350",
        "root": "nitch-0.1.6",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/nitch/copyright",
        "binary": "https://github.com/ssleert/nitch/releases/download/0.1.6/nitchNoNerd",
        "binary_sha256": "0f88f0dd30341476e62f2265bf81a67182a6a29e89003e0a19a15c666ba4f295",
        "description": "Fast system information tool written in Nim",
        "license": "MIT",
    },
    "nushell": {
        "version": "0.116.1",
        "arch": "x86_64",
        "url": "https://github.com/nushell/nushell",
        "archive": "https://codeload.github.com/nushell/nushell/tar.gz/refs/tags/0.116.1",
        "archive_sha256": "0cca0c5bc9d9eb608dee00c75b6b511917df6e66c784b034468bab2ff0fbb9b4",
        "root": "nushell-0.116.1",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/nushell/copyright",
        "binary_archive": "https://github.com/nushell/nushell/releases/download/0.116.1/nu-0.116.1-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "13ef75df08df30f8d7b031df538e82a52c6e74ce839b1a9091c3a3f1969c3742",
        "binary_members": {
            "nu-0.116.1-x86_64-unknown-linux-musl/nu": "usr/bin/nu",
        },
        "binary_member_sha256": {
            "nu-0.116.1-x86_64-unknown-linux-musl/nu": "9afb8f05657a67fae75c5aef97c539c7e2a261f50163e80bdb08a348ccba3767",
        },
        "files": {
            "README.md": "usr/share/doc/nushell/README.md",
        },
        "provides": ["nu=/usr/bin/nu"],
        "description": "Modern shell with structured data pipelines",
        "license": "MIT",
    },
    "jujutsu": {
        "version": "0.46.0",
        "arch": "x86_64",
        "url": "https://github.com/jj-vcs/jj",
        "archive": "https://codeload.github.com/jj-vcs/jj/tar.gz/refs/tags/v0.46.0",
        "archive_sha256": "6489f79d59dc4f9c11230c51d309dc9c6ec392921772b738546966c494b6d72c",
        "root": "jj-0.46.0",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/jujutsu/copyright",
        "binary_archive": "https://github.com/jj-vcs/jj/releases/download/v0.46.0/jj-v0.46.0-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "fce0271158e665ceb82dc66c5eb95b1728649094e9133074f3b5baece345fc08",
        "binary_members": {"./jj": "usr/bin/jj"},
        "binary_member_sha256": {
            "./jj": "dbf80e7290f00005908aaa9aa576fd1906156049af0fba94d2dec48ff4193534",
        },
        "files": {
            "README.md": "usr/share/doc/jujutsu/README.md",
            "CHANGELOG.md": "usr/share/doc/jujutsu/CHANGELOG.md",
        },
        "provides": ["jj=/usr/bin/jj"],
        "dependencies": ["git>=2.42.0"],
        "description": "Git-compatible version control system with operation history and undo",
        "license": "Apache-2.0",
    },
    "mcfly": {
        "version": "0.9.4",
        "arch": "x86_64",
        "url": "https://github.com/cantino/mcfly",
        "archive": "https://codeload.github.com/cantino/mcfly/tar.gz/refs/tags/v0.9.4",
        "archive_sha256": "31cdd76bfab3b05b4873bc20f03eb022e5a5d68f6595bc6df5dd9fce4b519e53",
        "root": "mcfly-0.9.4",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/mcfly/copyright",
        "binary_archive": "https://github.com/cantino/mcfly/releases/download/v0.9.4/mcfly-v0.9.4-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "72d2c6fdaa111ac96c2cf725fc40e313e2856643482be58608911a09440313f1",
        "binary_members": {"mcfly": "usr/bin/mcfly"},
        "binary_member_sha256": {
            "mcfly": "8e854e63ecc1b730b0c8183fbbd931739d18d7cf1545752d4436812b112b4ada",
        },
        "files": {
            "README.md": "usr/share/doc/mcfly/README.md",
            "CHANGELOG.txt": "usr/share/doc/mcfly/CHANGELOG.txt",
        },
        "description": "Intelligent shell history search tool",
        "license": "MIT",
    },
    "curlie": {
        "version": "1.8.2",
        "arch": "x86_64",
        "url": "https://github.com/rs/curlie",
        "archive": "https://codeload.github.com/rs/curlie/tar.gz/refs/tags/v1.8.2",
        "archive_sha256": "846ca3c5f2cca60c15eaef24949cf49607f09bdd68cbe9d81a2a026e434fa715",
        "root": "curlie-1.8.2",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/curlie/copyright",
        "binary_archive": "https://github.com/rs/curlie/releases/download/v1.8.2/curlie_1.8.2_linux_amd64.tar.gz",
        "binary_archive_sha256": "033d39a06d7a4320f923c49b488b89ec415c1901413ae85d26b9f7a09b38f1a1",
        "binary_members": {"curlie": "usr/bin/curlie"},
        "binary_member_sha256": {
            "curlie": "7fd0c57bf5df648e72139a9a2281f443f877f7916074a8dd0dba18722d491974",
        },
        "files": {"README.md": "usr/share/doc/curlie/README.md"},
        "dependencies": ["curl-cli"],
        "description": "HTTPie-style command-line interface for curl",
        "license": "MIT",
    },
    "atuin": {
        "version": "18.24.2",
        "arch": "x86_64",
        "url": "https://github.com/atuinsh/atuin",
        "archive": "https://github.com/atuinsh/atuin/releases/download/v18.24.2/atuin-x86_64-unknown-linux-musl.tar.gz",
        "archive_sha256": "311cd1ba4b8be27de7071dbcae1c95ff4c2b38aeec82d557707b6b832bfceddf",
        "root": "atuin-x86_64-unknown-linux-musl",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/atuin/copyright",
        "binary_members": {
            "atuin-x86_64-unknown-linux-musl/atuin": "usr/bin/atuin",
        },
        "binary_member_sha256": {
            "atuin-x86_64-unknown-linux-musl/atuin": "3a80d8d27a6c4317843611a08c60ad7044d8e5b845bdcf896f8564f0ed9da383",
        },
        "files": {
            "README.md": "usr/share/doc/atuin/README.md",
            "CHANGELOG.md": "usr/share/doc/atuin/CHANGELOG.md",
        },
        "description": "Searchable shell history manager with optional sync",
        "license": "MIT",
    },
    "yazi": {
        "version": "26.9.1",
        "arch": "x86_64",
        "url": "https://github.com/sxyazi/yazi",
        "archive": "https://codeload.github.com/sxyazi/yazi/tar.gz/refs/tags/v26.9.1",
        "archive_sha256": "66857f1b670469daf258edd0bb2ea51d9ad3e2cab4eea9684028c80059fd6862",
        "root": "yazi-26.9.1",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/yazi/copyright",
        "binary_archive": "https://github.com/sxyazi/yazi/releases/download/v26.9.1/yazi-x86_64-unknown-linux-musl.zip",
        "binary_archive_sha256": "9b9c39decccf8cb0ff53a7d637d38f8a79d93bbd0099f4ea9c619ef6bb392f5d",
        "binary_members": {
            "yazi-x86_64-unknown-linux-musl/yazi": "usr/bin/yazi",
            "yazi-x86_64-unknown-linux-musl/ya": "usr/bin/ya",
        },
        "binary_member_sha256": {
            "yazi-x86_64-unknown-linux-musl/yazi": "a780480f4de6991f0ef3925a06af7835163a22efe52d73b5bc1fb827aa76ed11",
            "yazi-x86_64-unknown-linux-musl/ya": "d2d8c74cd1434864a873bf645a9dd211d8ede8dd877861d622c9a2ecd7deb38d",
        },
        "binary_files": {
            "yazi-x86_64-unknown-linux-musl/completions/yazi.bash": "usr/share/bash-completion/completions/yazi",
            "yazi-x86_64-unknown-linux-musl/completions/ya.bash": "usr/share/bash-completion/completions/ya",
            "yazi-x86_64-unknown-linux-musl/completions/yazi.fish": "usr/share/fish/vendor_completions.d/yazi.fish",
            "yazi-x86_64-unknown-linux-musl/completions/ya.fish": "usr/share/fish/vendor_completions.d/ya.fish",
            "yazi-x86_64-unknown-linux-musl/completions/_yazi": "usr/share/zsh/site-functions/_yazi",
            "yazi-x86_64-unknown-linux-musl/completions/_ya": "usr/share/zsh/site-functions/_ya",
        },
        "files": {
            "README.md": "usr/share/doc/yazi/README.md",
            "LICENSE-ICONS": "usr/share/doc/yazi/LICENSE-ICONS",
        },
        "provides": ["yazi=/usr/bin/yazi", "ya=/usr/bin/ya"],
        "description": "Fast terminal file manager with shell completions",
        "license": "MIT",
    },
    "zellij": {
        "version": "0.45.1",
        "arch": "x86_64",
        "url": "https://github.com/zellij-org/zellij",
        "archive": "https://codeload.github.com/zellij-org/zellij/tar.gz/refs/tags/v0.45.1",
        "archive_sha256": "5cbe711437d2a61afd9287165f6aca0bcccb9ab1473633665a5b11ed55467852",
        "root": "zellij-0.45.1",
        "license_source": "LICENSE.md",
        "license_path": "usr/share/doc/zellij/copyright",
        "binary_archive": "https://github.com/zellij-org/zellij/releases/download/v0.45.1/zellij-no-web-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "d7bda1e18c30a688833ae7627f1d6a253bbba5349a4bc48e4f0ec008aaf75ed1",
        "binary_members": {"zellij": "usr/bin/zellij"},
        "binary_member_sha256": {
            "zellij": "0ec6ef07b63c6355c02ce18343d40ef5ef5af19e25313ea9009c8fceda29e94f",
        },
        "files": {
            "README.md": "usr/share/doc/zellij/README.md",
            "CHANGELOG.md": "usr/share/doc/zellij/CHANGELOG.md",
        },
        "description": "Terminal workspace manager and multiplexer without the web client",
        "license": "MIT",
    },
    "gum": {
        "version": "2.0.2",
        "arch": "x86_64",
        "url": "https://github.com/charmbracelet/gum",
        "archive": "https://codeload.github.com/charmbracelet/gum/tar.gz/refs/tags/v2.0.2",
        "archive_sha256": "06403707671e9b2af386640d8b9f6079efc0aadf77e8f6b091bd191fe16c1264",
        "root": "gum-2.0.2",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/gum/copyright",
        "binary_archive": "https://github.com/charmbracelet/gum/releases/download/v2.0.2/gum_2.0.2_Linux_x86_64.tar.gz",
        "binary_archive_sha256": "d842e06d93dbed90af48cb8dd10698db6f22e331fc40346bb37bbc753109edc2",
        "binary_members": {"gum_2.0.2_Linux_x86_64/gum": "usr/bin/gum"},
        "binary_member_sha256": {
            "gum_2.0.2_Linux_x86_64/gum": "279fb12ba05ebfec7ef77707c3874800b42acdc4d62f83d854b780d91db5e034",
        },
        "binary_files": {
            "gum_2.0.2_Linux_x86_64/completions/gum.bash": "usr/share/bash-completion/completions/gum",
            "gum_2.0.2_Linux_x86_64/completions/gum.fish": "usr/share/fish/vendor_completions.d/gum.fish",
            "gum_2.0.2_Linux_x86_64/completions/gum.zsh": "usr/share/zsh/site-functions/_gum",
            "gum_2.0.2_Linux_x86_64/manpages/gum.1.gz": "usr/share/man/man1/gum.1.gz",
        },
        "files": {"README.md": "usr/share/doc/gum/README.md"},
        "description": "Interactive command-line components for shell scripts",
        "license": "MIT",
    },
    "vivid": {
        "version": "0.11.1",
        "arch": "x86_64",
        "url": "https://github.com/sharkdp/vivid",
        "archive": "https://codeload.github.com/sharkdp/vivid/tar.gz/refs/tags/v0.11.1",
        "archive_sha256": "a43ccfbc6554055181a08f2740664f9280fa2d0e57c4641850c60dd0e5323720",
        "root": "vivid-0.11.1",
        "license_source": "LICENSE-MIT",
        "license_path": "usr/share/doc/vivid/copyright",
        "binary_archive": "https://github.com/sharkdp/vivid/releases/download/v0.11.1/vivid-v0.11.1-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "f1a80a39c75ceae43c4187f302e66c0a45d941ed1479456a0d5ddef665663181",
        "binary_members": {
            "vivid-v0.11.1-x86_64-unknown-linux-musl/vivid": "usr/bin/vivid",
        },
        "binary_member_sha256": {
            "vivid-v0.11.1-x86_64-unknown-linux-musl/vivid": "fe60aef5962d6c3fb9974715054fa261a11f207327d94d11bf09947b0e154caf",
        },
        "files": {
            "README.md": "usr/share/doc/vivid/README.md",
            "CHANGELOG.md": "usr/share/doc/vivid/CHANGELOG.md",
            "LICENSE-APACHE": "usr/share/doc/vivid/LICENSE-APACHE",
        },
        "description": "LS_COLORS generator with configurable terminal color themes",
        "license": "MIT/Apache-2.0",
    },
    "ouch": {
        "version": "0.8.3",
        "arch": "x86_64",
        "url": "https://github.com/ouch-org/ouch",
        "archive": "https://codeload.github.com/ouch-org/ouch/tar.gz/refs/tags/0.8.3",
        "archive_sha256": "f695393cbbd89cf5a2095c32235e585a85432ccfb902c78d2a2e9787abbb439c",
        "root": "ouch-0.8.3",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/ouch/copyright",
        "binary_archive": "https://github.com/ouch-org/ouch/releases/download/0.8.3/ouch-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "eaab9b997a823f584557ac5f85205105ae577a4c4fbd23926b686469cfa19881",
        "binary_members": {
            "ouch-x86_64-unknown-linux-musl/ouch": "usr/bin/ouch",
        },
        "binary_member_sha256": {
            "ouch-x86_64-unknown-linux-musl/ouch": "08cc9ca308cb69a8a652cc0bb32b7ee9cb8c04b1c6cf085fdf3d643a2b1a3ea1",
        },
        "binary_files": {
            "ouch-x86_64-unknown-linux-musl/completions/ouch.bash": "usr/share/bash-completion/completions/ouch",
            "ouch-x86_64-unknown-linux-musl/completions/ouch.fish": "usr/share/fish/vendor_completions.d/ouch.fish",
            "ouch-x86_64-unknown-linux-musl/completions/_ouch": "usr/share/zsh/site-functions/_ouch",
            "ouch-x86_64-unknown-linux-musl/man/ouch.1": "usr/share/man/man1/ouch.1",
            "ouch-x86_64-unknown-linux-musl/man/ouch-compress.1": "usr/share/man/man1/ouch-compress.1",
            "ouch-x86_64-unknown-linux-musl/man/ouch-decompress.1": "usr/share/man/man1/ouch-decompress.1",
            "ouch-x86_64-unknown-linux-musl/man/ouch-list.1": "usr/share/man/man1/ouch-list.1",
        },
        "files": {"README.md": "usr/share/doc/ouch/README.md"},
        "description": "Command-line utility for compressing and extracting archives",
        "license": "MIT",
    },
    "navi": {
        "version": "2.24.0",
        "arch": "x86_64",
        "url": "https://github.com/denisidoro/navi",
        "archive": "https://codeload.github.com/denisidoro/navi/tar.gz/refs/tags/v2.24.0",
        "archive_sha256": "4c10f47c306826255b07483b7e94eed8ffc1401555c52434a56246295d3f2728",
        "root": "navi-2.24.0",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/navi/copyright",
        "binary_archive": "https://github.com/denisidoro/navi/releases/download/v2.24.0/navi-v2.24.0-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "f334047479241f7c8b48d091f4e814dcc798a51a74dfd04ee63cbf67e8c921ba",
        "binary_members": {"./navi": "usr/bin/navi"},
        "binary_member_sha256": {
            "./navi": "c643ff670ced4b47887c887ff0877c156497642b8d25dd9ae279d1d68c27e9bb",
        },
        "files": {
            "README.md": "usr/share/doc/navi/README.md",
            "docs/installation.md": "usr/share/doc/navi/installation.md",
            "docs/cheat_example.cheat": "usr/share/doc/navi/cheat_example.cheat",
            "shell/navi.plugin.bash": "usr/share/navi/shell/navi.plugin.bash",
            "shell/navi.plugin.zsh": "usr/share/navi/shell/navi.plugin.zsh",
        },
        "dependencies": ["fzf"],
        "description": "Interactive command-line cheatsheet browser with shell integration",
        "license": "Apache-2.0",
    },
    "choose": {
        "version": "1.3.7",
        "arch": "x86_64",
        "url": "https://github.com/theryangeary/choose",
        "archive": "https://codeload.github.com/theryangeary/choose/tar.gz/refs/tags/v1.3.7",
        "archive_sha256": "8f51a315fbbe0688c4a2078ba8bc8446d36943b6cce6ed9bbd6a11f33bd1a134",
        "root": "choose-1.3.7",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/choose/copyright",
        "binary": "https://github.com/theryangeary/choose/releases/download/v1.3.7/choose-x86_64-unknown-linux-musl",
        "binary_sha256": "f9958b15b9c5e2ed21162dcd21e514b51a03efc8dcbf730546c3dbef8a2eba2a",
        "files": {"readme.md": "usr/share/doc/choose/README.md"},
        "description": "Lightweight command-line tool for selecting fields from text",
        "license": "GPL-3.0-or-later",
    },
    "dysk": {
        "version": "3.8.1",
        "arch": "x86_64",
        "url": "https://github.com/Canop/dysk",
        "archive": "https://codeload.github.com/Canop/dysk/tar.gz/refs/tags/v3.8.1",
        "archive_sha256": "8da0e478a6027803043149c63c84abbb0dd4b3538372b8d854c2f8cab8215f41",
        "root": "dysk-3.8.1",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/dysk/copyright",
        "binary_archive": "https://github.com/Canop/dysk/releases/download/v3.8.1/dysk_3.8.1.zip",
        "binary_archive_sha256": "e983c4863d370ba143cab95abeb520c56a22b98c83b647db828614592a9939a4",
        "binary_members": {
            "x86_64-unknown-linux-musl/dysk": "usr/bin/dysk",
        },
        "binary_member_sha256": {
            "x86_64-unknown-linux-musl/dysk": "82dc7233e298e0eb16c94f48eec102275b3efe18265758bfe742ba13736ddb9c",
        },
        "binary_files": {
            "completion/dysk.bash": "usr/share/bash-completion/completions/dysk",
            "completion/dysk.fish": "usr/share/fish/vendor_completions.d/dysk.fish",
            "completion/_dysk": "usr/share/zsh/site-functions/_dysk",
            "completion/dysk.elv": "usr/share/elvish/lib/dysk.elv",
            "man/dysk.1": "usr/share/man/man1/dysk.1",
            "README.md": "usr/share/doc/dysk/README.md",
            "CHANGELOG.md": "usr/share/doc/dysk/CHANGELOG.md",
        },
        "description": "Terminal filesystem usage viewer with shell completions and a manual",
        "license": "MIT",
    },
    "doggo": {
        "version": "1.4.2",
        "arch": "x86_64",
        "url": "https://github.com/mr-karan/doggo",
        "archive": "https://codeload.github.com/mr-karan/doggo/tar.gz/refs/tags/v1.4.2",
        "archive_sha256": "9c5d1d0d800f72e80d35f6731fa3a80435f5a1af08af900a6b77142600e19395",
        "root": "doggo-1.4.2",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/doggo/copyright",
        "binary_archive": "https://github.com/mr-karan/doggo/releases/download/v1.4.2/doggo-linux-x86_64.tar.gz",
        "binary_archive_sha256": "634d88c92e1e98a8e5f6bc9758ca39a797f4237e4c542156e48c441be702742c",
        "binary_members": {"doggo": "usr/bin/doggo"},
        "binary_member_sha256": {
            "doggo": "74b7a8d268f657a281468d2f2955e19575f31c8bdb001168da2d6a9063c11acd",
        },
        "files": {"README.md": "usr/share/doc/doggo/README.md"},
        "description": "DNS query client with plain-text and structured output",
        "license": "GPL-3.0-only",
    },
    "trippy": {
        "version": "0.13.0",
        "arch": "x86_64",
        "url": "https://github.com/fujiapple852/trippy",
        "archive": "https://codeload.github.com/fujiapple852/trippy/tar.gz/refs/tags/0.13.0",
        "archive_sha256": "72e598d2e0b947e8bc46706021c511f169b7e7634a734c326e492e0f30725c35",
        "root": "trippy-0.13.0",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/trippy/copyright",
        "binary_archive": "https://github.com/fujiapple852/trippy/releases/download/0.13.0/trippy-0.13.0-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "aa2b7b2a0773f3cc04da691100419049950b690fe3736d25e24ac955b63d6056",
        "binary_members": {
            "trippy-0.13.0-x86_64-unknown-linux-musl/trip": "usr/bin/trip",
        },
        "binary_member_sha256": {
            "trippy-0.13.0-x86_64-unknown-linux-musl/trip": "ef20e94ec9620eb81b4cfea409c68e9fd522a10200fb1e9de7a1b6bfe26f620d",
        },
        "files": {
            "README.md": "usr/share/doc/trippy/README.md",
            "CHANGELOG.md": "usr/share/doc/trippy/CHANGELOG.md",
        },
        "provides": ["trip=/usr/bin/trip"],
        "description": "Terminal traceroute and network path diagnostic tool",
        "license": "Apache-2.0",
    },
    "btm": {
        "version": "0.14.9",
        "arch": "x86_64",
        "url": "https://github.com/ClementTsang/bottom",
        "archive": "https://codeload.github.com/ClementTsang/bottom/tar.gz/refs/tags/0.14.9",
        "archive_sha256": "1dbb940c763fb583b7e1c7dfa165b73ed9a0ba712e72cc97311c5b1c098d5b72",
        "root": "bottom-0.14.9",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/btm/copyright",
        "binary_archive": "https://github.com/ClementTsang/bottom/releases/download/0.14.9/bottom_x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "b4bee5b193e7d3f6e090ac14f0ca15acec2e7fe4ef64988e0bfc492e16c28c9a",
        "binary_members": {"btm": "usr/bin/btm"},
        "binary_member_sha256": {
            "btm": "5bffbcd391b9b9964e8429549f1c404f25d7606a5031a4242cad787ac6028bf3",
        },
        "binary_files": {
            "completion/btm.bash": "usr/share/bash-completion/completions/btm",
            "completion/btm.fish": "usr/share/fish/vendor_completions.d/btm.fish",
            "completion/_btm": "usr/share/zsh/site-functions/_btm",
        },
        "files": {"README.md": "usr/share/doc/btm/README.md"},
        "description": "Interactive terminal process and system monitor",
        "license": "MIT",
    },
    "dua": {
        "version": "2.44.0",
        "arch": "x86_64",
        "url": "https://github.com/Byron/dua-cli",
        "archive": "https://codeload.github.com/Byron/dua-cli/tar.gz/refs/tags/v2.44.0",
        "archive_sha256": "84fcc09a982542037e990bc3cf861b2e6ef34a556ad45552eceda3bb02801566",
        "root": "dua-cli-2.44.0",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/dua/copyright",
        "binary_archive": "https://github.com/Byron/dua-cli/releases/download/v2.44.0/dua-v2.44.0-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "e2219f722f93fbe7069df282f2bf33e7a524292ef43ca5fe27b25cf3fe158a3d",
        "binary_members": {
            "dua-v2.44.0-x86_64-unknown-linux-musl/dua": "usr/bin/dua",
        },
        "binary_member_sha256": {
            "dua-v2.44.0-x86_64-unknown-linux-musl/dua": "efd148a50ee6921ff1bb953f0618a58225507a4aa954ad539f7d3dd7f2c6a2a8",
        },
        "files": {
            "README.md": "usr/share/doc/dua/README.md",
            "CHANGELOG.md": "usr/share/doc/dua/CHANGELOG.md",
        },
        "description": "Interactive disk usage analyzer with subtree deletion and snapshots",
        "license": "MIT",
    },
    "gitui": {
        "version": "0.28.1",
        "arch": "x86_64",
        "url": "https://github.com/gitui-org/gitui",
        "archive": "https://codeload.github.com/gitui-org/gitui/tar.gz/refs/tags/v0.28.1",
        "archive_sha256": "0400cbf59605490b5fb8779f9af41fa4d7a1bb748093ca0e13156a5dff31c7aa",
        "root": "gitui-0.28.1",
        "license_source": "LICENSE.md",
        "license_path": "usr/share/doc/gitui/copyright",
        "binary_archive": "https://github.com/gitui-org/gitui/releases/download/v0.28.1/gitui-linux-x86_64.tar.gz",
        "binary_archive_sha256": "f6149b9ae203397158b0c89c13cfde718e7121d3d3cd2ebc597f93d6628d9b5b",
        "binary_members": {"./gitui": "usr/bin/gitui"},
        "binary_member_sha256": {
            "./gitui": "bebbfb2b178c007078cd3f0002209fbd773061cf3b5fc6d8f4ef0e7d46d22397",
        },
        "files": {
            "README.md": "usr/share/doc/gitui/README.md",
            "CHANGELOG.md": "usr/share/doc/gitui/CHANGELOG.md",
        },
        "dependencies": ["git"],
        "description": "Terminal user interface for Git repositories",
        "license": "MIT",
    },
    "watchexec": {
        "version": "2.8.0",
        "arch": "x86_64",
        "url": "https://github.com/watchexec/watchexec",
        "archive": "https://codeload.github.com/watchexec/watchexec/tar.gz/refs/tags/v2.8.0",
        "archive_sha256": "861496cd1e7d8546f770551893077e9b898fef1a6623d5e2207cf28c932e7738",
        "root": "watchexec-2.8.0",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/watchexec/copyright",
        "binary_archive": "https://github.com/watchexec/watchexec/releases/download/v2.8.0/watchexec-2.8.0-x86_64-unknown-linux-musl.tar.xz",
        "binary_archive_sha256": "35ea667cbe4bc96c23ba2a1f46c94c46bc385b67165e3bf621c0e7e4959aa99b",
        "binary_members": {
            "watchexec-2.8.0-x86_64-unknown-linux-musl/watchexec": "usr/bin/watchexec",
        },
        "binary_member_sha256": {
            "watchexec-2.8.0-x86_64-unknown-linux-musl/watchexec": "74c1004a62052b3785ab1267a2270671185d16f33e60d092c4e1c4f41678e0c2",
        },
        "binary_files": {
            "watchexec-2.8.0-x86_64-unknown-linux-musl/watchexec.1": "usr/share/man/man1/watchexec.1",
            "watchexec-2.8.0-x86_64-unknown-linux-musl/completions/bash": "usr/share/bash-completion/completions/watchexec",
            "watchexec-2.8.0-x86_64-unknown-linux-musl/completions/fish": "usr/share/fish/vendor_completions.d/watchexec.fish",
            "watchexec-2.8.0-x86_64-unknown-linux-musl/completions/zsh": "usr/share/zsh/site-functions/_watchexec",
        },
        "files": {"README.md": "usr/share/doc/watchexec/README.md"},
        "description": "Run commands when files change with shell completions and a manual",
        "license": "Apache-2.0",
    },
    "git-delta": {
        "version": "0.20.1",
        "arch": "x86_64",
        "url": "https://github.com/dandavison/delta",
        "archive": "https://codeload.github.com/dandavison/delta/tar.gz/refs/tags/0.20.1",
        "archive_sha256": "d9d502396e3595ee8fd926f1ed2e54ac9935baabd3569a3753efab36304f90fa",
        "root": "delta-0.20.1",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/git-delta/copyright",
        "binary_archive": "https://github.com/dandavison/delta/releases/download/0.20.1/delta-0.20.1-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "69dcb4043f55c29118f408c6efd30115e0a4e0a6709253343b8c8fa814ab4a31",
        "binary_members": {
            "delta-0.20.1-x86_64-unknown-linux-musl/delta": "usr/bin/delta",
        },
        "binary_member_sha256": {
            "delta-0.20.1-x86_64-unknown-linux-musl/delta": "35d5a370dd8f554c0465a37acbd1016b8781efa10e04291f8fffc59cbc9e83c4",
        },
        "files": {
            "README.md": "usr/share/doc/git-delta/README.md",
            "themes.gitconfig": "usr/share/git-delta/themes.gitconfig",
        },
        "provides": ["delta=/usr/bin/delta"],
        "description": "Syntax-highlighting pager for git, diff, grep, and blame output",
        "license": "MIT",
    },
    "cargo-nextest": {
        "version": "0.9.150",
        "arch": "x86_64",
        "url": "https://github.com/nextest-rs/nextest",
        "archive": "https://codeload.github.com/nextest-rs/nextest/tar.gz/refs/tags/cargo-nextest-0.9.150",
        "archive_sha256": "52daf837e45bf08815f1fa92202adb28d835c04b3e8f1fc9a2d1b47e2b6537c6",
        "root": "nextest-cargo-nextest-0.9.150",
        "license_source": "LICENSE-MIT",
        "license_path": "usr/share/doc/cargo-nextest/copyright",
        "binary_archive": "https://github.com/nextest-rs/nextest/releases/download/cargo-nextest-0.9.150/cargo-nextest-0.9.150-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "84f0f9ba75d0b6fa117bc418d49747ba78e20e4583057f6fa628f1b17c17234d",
        "binary_members": {"cargo-nextest": "usr/bin/cargo-nextest"},
        "binary_member_sha256": {
            "cargo-nextest": "f60be355cded290b1080a0b7c92ca11ec34a1900e3ad1afbd6336ddbfdbbded0",
        },
        "files": {
            "README.md": "usr/share/doc/cargo-nextest/README.md",
            "LICENSE-MIT": "usr/share/licenses/cargo-nextest/LICENSE-MIT",
            "LICENSE-APACHE": "usr/share/licenses/cargo-nextest/LICENSE-APACHE",
        },
        "dependencies": ["cargo"],
        "description": "Fast parallel test runner for Rust Cargo projects",
        "license": "Apache-2.0 OR MIT",
    },
    "cargo-deny": {
        "version": "0.20.2",
        "arch": "x86_64",
        "url": "https://github.com/EmbarkStudios/cargo-deny",
        "archive": "https://codeload.github.com/EmbarkStudios/cargo-deny/tar.gz/refs/tags/0.20.2",
        "archive_sha256": "31fc1d33fae8d5b4264db8ed5c6c070c27cd2f869cc6d2983ac03548c5a81c8e",
        "root": "cargo-deny-0.20.2",
        "license_source": "LICENSE-MIT",
        "license_path": "usr/share/doc/cargo-deny/copyright",
        "binary_archive": "https://github.com/EmbarkStudios/cargo-deny/releases/download/0.20.2/cargo-deny-0.20.2-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "9f12ed4c49936e09b48bf862b595cde2fe64fcbd9d74dfacac6131ca824c8d5f",
        "binary_members": {
            "cargo-deny-0.20.2-x86_64-unknown-linux-musl/cargo-deny": "usr/bin/cargo-deny",
        },
        "binary_member_sha256": {
            "cargo-deny-0.20.2-x86_64-unknown-linux-musl/cargo-deny": "b329e25933d01c36dd7c47d84ea5716694f9b7caf53a5003d45674703a8ed54a",
        },
        "files": {
            "README.md": "usr/share/doc/cargo-deny/README.md",
            "CHANGELOG.md": "usr/share/doc/cargo-deny/CHANGELOG.md",
            "LICENSE-MIT": "usr/share/licenses/cargo-deny/LICENSE-MIT",
            "LICENSE-APACHE": "usr/share/licenses/cargo-deny/LICENSE-APACHE",
        },
        "dependencies": ["cargo"],
        "description": "Cargo subcommand for dependency, license, and source policy checks",
        "license": "MIT OR Apache-2.0",
    },
    "cargo-audit": {
        "version": "0.22.2",
        "arch": "x86_64",
        "url": "https://github.com/RustSec/rustsec",
        "archive": "https://codeload.github.com/RustSec/rustsec/tar.gz/refs/tags/cargo-audit%2Fv0.22.2",
        "archive_sha256": "85c368a4d166b2cc4972108d50abc5fad605013b65098929a06122439488beb5",
        "root": "rustsec-cargo-audit-v0.22.2",
        "license_source": "cargo-audit/LICENSE-MIT",
        "license_path": "usr/share/doc/cargo-audit/copyright",
        "binary_archive": "https://github.com/RustSec/rustsec/releases/download/cargo-audit/v0.22.2/cargo-audit-x86_64-unknown-linux-musl-v0.22.2.tgz",
        "binary_archive_sha256": "7fb9497f8594b389e5fce5ef9b92db08432996895b2e0c5a0167a69ed445c428",
        "binary_members": {
            "cargo-audit-x86_64-unknown-linux-musl-v0.22.2/cargo-audit": "usr/bin/cargo-audit",
        },
        "binary_member_sha256": {
            "cargo-audit-x86_64-unknown-linux-musl-v0.22.2/cargo-audit": "66f9c8f530950d106c1869ce27ef5a4008bcea53c7716c3ed8117586337ee7d8",
        },
        "files": {
            "cargo-audit/README.md": "usr/share/doc/cargo-audit/README.md",
            "cargo-audit/CHANGELOG.md": "usr/share/doc/cargo-audit/CHANGELOG.md",
            "cargo-audit/LICENSE-MIT": "usr/share/licenses/cargo-audit/LICENSE-MIT",
            "cargo-audit/LICENSE-APACHE": "usr/share/licenses/cargo-audit/LICENSE-APACHE",
        },
        "dependencies": ["cargo", "git"],
        "description": "Cargo subcommand to scan Rust dependencies for known vulnerabilities",
        "license": "Apache-2.0 OR MIT",
    },
    "yt-dlp": {
        "version": "2026.08.19",
        "arch": "any",
        "url": "https://github.com/yt-dlp/yt-dlp",
        "archive": "https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp.tar.gz",
        "archive_sha256": "072aad4f2a7604e92155f61a275a4752dc64046c8f6d90df3710525d94cd37c1",
        "root": "yt-dlp",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/yt-dlp/copyright",
        "binary": "https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp",
        "binary_sha256": "1fa6733c37ea6fb51c99ad8fe785e7b7e5f3246c9b980230329d4fb72ed8d4d6",
        "binary_path": "usr/bin/yt-dlp",
        "files": {
            "yt-dlp.1": "usr/share/man/man1/yt-dlp.1",
            "README.md": "usr/share/doc/yt-dlp/README.md",
            "completions/bash/yt-dlp": "usr/share/bash-completion/completions/yt-dlp",
            "completions/fish/yt-dlp.fish": "usr/share/fish/vendor_completions.d/yt-dlp.fish",
            "completions/zsh/_yt-dlp": "usr/share/zsh/site-functions/_yt-dlp",
        },
        "description": "Feature-rich command-line audio and video downloader",
        "license": "Unlicense; bundled zipimport code also includes MIT and ISC components",
    },
    "oha": {
        "version": "1.16.0",
        "arch": "x86_64",
        "url": "https://github.com/hatoo/oha",
        "archive": "https://codeload.github.com/hatoo/oha/tar.gz/refs/tags/v1.16.0",
        "archive_sha256": "8d856e2850efb521c0a1f8efed530eeaeebea34d09c6edc19a42dc5e13b14287",
        "root": "oha-1.16.0",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/oha/copyright",
        "binary": "https://github.com/hatoo/oha/releases/download/v1.16.0/oha-linux-amd64",
        "binary_sha256": "620bb9e16fb53eabc9a3fc45f88bdb41fefa3fee5c05e75892011ce320391716",
        "binary_path": "usr/bin/oha",
        "files": {
            "README.md": "usr/share/doc/oha/README.md",
            "CHANGELOG.md": "usr/share/doc/oha/CHANGELOG.md",
        },
        "description": "HTTP load generator with a real-time terminal dashboard",
        "license": "MIT",
    },
    "scc": {
        "version": "4.1.0",
        "arch": "x86_64",
        "url": "https://github.com/boyter/scc",
        "archive": "https://codeload.github.com/boyter/scc/tar.gz/refs/tags/v4.1.0",
        "archive_sha256": "4f3cf36010c542b10d5582afb91c668b26889160b184deee21b4319347030a7c",
        "root": "scc-4.1.0",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/scc/copyright",
        "binary_archive": "https://github.com/boyter/scc/releases/download/v4.1.0/scc_Linux_x86_64.tar.gz",
        "binary_archive_sha256": "c7328436d3027f4357d3d7853f7dc3ac2bbcb4ca08f1adad91a27c593884079b",
        "binary_members": {"scc": "usr/bin/scc"},
        "binary_member_sha256": {
            "scc": "4763d7437218d2d642e2f2a4f7f078e044aa67e0e15b90bab6c70d5fc0ab4022"
        },
        "files": {"README.md": "usr/share/doc/scc/README.md"},
        "description": "Fast source code counter with language and complexity metrics",
        "license": "MIT",
    },
}
INTERPRETER = re.compile(r"Requesting program interpreter: (.+?)\]")
NEEDED = re.compile(r"Shared library: \[(.+?)\]")
MAX_ARCHIVE_BYTES = 90_000_000
MAX_UPSTREAM_MEMBER_BYTES = 64 * 1024 * 1024
MAX_BINARY_MEMBER_BYTES = 80 * 1024 * 1024
MAX_PACKAGE_METADATA_BYTES = 64 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_cache(cache: Path) -> Path:
    cache = cache.absolute()
    # Check parents too: another user must not be able to replace the cache.
    for directory in reversed((cache, *cache.parents)):
        directory.mkdir(mode=0o700, exist_ok=True)
        metadata = directory.lstat()
        allowed_owners = {os.geteuid()} if directory == cache else {0, os.geteuid()}
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in allowed_owners:
            raise ValueError(f"unsafe source cache directory: {directory}")
        # A trusted sticky parent, such as /tmp, protects its owned children.
        if metadata.st_mode & 0o022 and (
            directory == cache or not metadata.st_mode & stat.S_ISVTX
        ):
            raise ValueError(
                f"unsafe source cache directory is writable by others: {directory}"
            )
    return cache


def download(url: str, expected: str, cache: Path, work: Path) -> Path:
    """Return verified bytes from a private snapshot, never from the shared cache."""
    cache = prepare_cache(cache)
    target = cache / url.rsplit("/", 1)[-1]
    snapshot = work / target.name
    if not target.is_symlink() and target.is_file():
        shutil.copyfile(target, snapshot)
        if sha256(snapshot) == expected:
            return snapshot
    request = urllib.request.Request(
        url, headers={"User-Agent": "arkpkg-repo-upstream-package-builder/1"}
    )
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=cache, prefix=f".{target.name}.", suffix=".download", delete=False
        ) as output:
            temporary = Path(output.name)
            for attempt in range(4):
                try:
                    output.seek(0)
                    output.truncate()
                    with urllib.request.urlopen(request, timeout=120) as response:
                        shutil.copyfileobj(response, output)
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    if attempt == 3:
                        raise
                    time.sleep(2**attempt)
        shutil.copyfile(temporary, snapshot)
        actual = sha256(snapshot)
        if actual != expected:
            snapshot.unlink(missing_ok=True)
            raise ValueError(
                f"SHA-256 mismatch for {target.name}: expected {expected}, got {actual}"
            )
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return snapshot


def read_archive_file(
    archive_path: Path, member_name: str, *, max_bytes: int | None = None
) -> bytes:
    """Read one regular archive member without extracting it to disk."""
    limit = MAX_UPSTREAM_MEMBER_BYTES if max_bytes is None else max_bytes
    member_path = PurePosixPath(member_name)
    if (
        not member_name
        or member_path.is_absolute()
        or "\\" in member_name
        or ".." in member_path.parts
    ):
        raise ValueError(f"unsafe upstream archive member: {member_name!r}")

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            try:
                member = archive.getinfo(member_name)
            except KeyError as error:
                raise ValueError(
                    f"{archive_path.name}: missing {member_name}"
                ) from error
            mode = (member.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            if member.is_dir() or (file_type and file_type != stat.S_IFREG):
                raise ValueError(
                    f"{archive_path.name}: {member_name} is not a regular file"
                )
            if not 0 <= member.file_size <= limit:
                raise ValueError(
                    f"{archive_path.name}: {member_name} exceeds "
                    f"{limit} bytes"
                )
            with archive.open(member) as source:
                data = source.read(limit + 1)
    else:
        with tarfile.open(archive_path, "r:*") as archive:
            try:
                member = archive.getmember(member_name)
            except KeyError as error:
                raise ValueError(
                    f"{archive_path.name}: missing {member_name}"
                ) from error
            if not member.isfile() or member.issparse():
                raise ValueError(
                    f"{archive_path.name}: {member_name} is not a regular file"
                )
            if not 0 <= member.size <= limit:
                raise ValueError(
                    f"{archive_path.name}: {member_name} exceeds "
                    f"{limit} bytes"
                )
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"{archive_path.name}: could not read {member_name}")
            with source:
                data = source.read(limit + 1)

    if len(data) > limit:
        raise ValueError(
            f"{archive_path.name}: {member_name} exceeds "
            f"{limit} bytes"
        )
    return data


def stage_has_library(staging: Path, soname: str) -> bool:
    directories = (
        "lib",
        "lib64",
        "lib/x86_64-linux-gnu",
        "usr/lib",
        "usr/lib64",
        "usr/lib/x86_64-linux-gnu",
    )
    return any(
        (staging / directory / soname).exists()
        or (staging / directory / soname).is_symlink()
        for directory in directories
    )


def validate_elf(binary: Path, staging: Path) -> None:
    dynamic = subprocess.run(
        ["readelf", "-d", str(binary)], capture_output=True, text=True, check=True
    ).stdout
    for line in dynamic.splitlines():
        match = NEEDED.search(line)
        if match and not stage_has_library(staging, match.group(1)):
            raise ValueError(
                f"{binary.name}: {match.group(1)} is absent from the ArkanaOS staging root"
            )
    program_headers = subprocess.run(
        ["readelf", "-l", str(binary)], capture_output=True, text=True, check=True
    ).stdout
    match = INTERPRETER.search(program_headers)
    if match:
        interpreter = PurePosixPath(match.group(1))
        staged_interpreter = staging.joinpath(*interpreter.parts[1:])
        if not staged_interpreter.exists() and not staged_interpreter.is_symlink():
            raise ValueError(
                f"{binary.name}: ELF interpreter {interpreter} is absent from staging"
            )


def archive_owners(repo_root: Path, ignored_packages: set[str]) -> set[str]:
    """Find payload paths owned by packages that are not being rebuilt."""
    owned = set()
    for archive_path in (repo_root / "packages").glob("*.ark"):
        package_name = None
        archive_owned = set()
        with (
            lz4.frame.open(archive_path, "rb") as compressed,
            tarfile.open(fileobj=compressed, mode="r|") as archive,
        ):
            for member in archive:
                if member.name == "ARKPKG" and member.isfile():
                    if not 0 <= member.size <= MAX_PACKAGE_METADATA_BYTES:
                        raise ValueError(
                            f"{archive_path.name}: package metadata exceeds "
                            f"{MAX_PACKAGE_METADATA_BYTES} bytes"
                        )
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError(
                            f"{archive_path.name}: could not read package metadata"
                        )
                    metadata = source.read(MAX_PACKAGE_METADATA_BYTES + 1)
                    if len(metadata) > MAX_PACKAGE_METADATA_BYTES:
                        raise ValueError(
                            f"{archive_path.name}: package metadata exceeds "
                            f"{MAX_PACKAGE_METADATA_BYTES} bytes"
                        )
                    for line in metadata.decode("utf-8").splitlines():
                        key, separator, value = line.partition(":")
                        if separator and key.strip() == "Name":
                            package_name = value.strip()
                            break
                if not (member.isfile() or member.issym() or member.islnk()):
                    continue
                path = PurePosixPath(member.name)
                if path.parts[:1] == ("package",) and len(path.parts) > 1:
                    archive_owned.add("/".join(path.parts[1:]))
        if package_name not in ignored_packages:
            owned.update(archive_owned)
    return owned


def recipe_text(name: str, package: dict[str, object]) -> str:
    """Render the package metadata file for an upstream release."""
    executable = "nitch" if name == "nitch" else name
    provides = package.get("provides") or [
        f"{executable}=/usr/bin/{executable}"
    ]
    fields = [
        f"Name: {name}",
        f"Description: {package['description']}",
        f"Version: {package['version']}",
        f"Arch: {package['arch']}",
        f"URL: {package['url']}",
        f"License: {package['license']}",
        "Maintainer: arkanaOS Team",
    ]
    dependencies = package.get("dependencies", [])
    if dependencies:
        fields.append("Dependencies:")
        fields.extend(f"  {item}" for item in dependencies)
    fields.extend((f"Provides: {', '.join(str(item) for item in provides)}", ""))
    return "\n".join(fields)


def write_payload_file(target: Path, data: bytes, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    target.chmod(mode)


def build(args: argparse.Namespace) -> None:
    """Build selected upstream CLI packages from checksum-pinned sources."""
    repo_root = args.repo_root.resolve()
    cache = prepare_cache(args.source_cache)
    staging = args.staging.resolve()
    selected = args.packages or sorted(PACKAGES)
    unknown = set(selected).difference(PACKAGES)
    if unknown:
        raise ValueError(f"unknown packages: {', '.join(sorted(unknown))}")
    owned = archive_owners(repo_root, set(selected))

    for index, name in enumerate(selected, 1):
        package = PACKAGES[name]
        with tempfile.TemporaryDirectory(prefix=f"arkpkg-{name}-") as temporary:
            work = Path(temporary) / "sources"
            work.mkdir()
            source_archive = download(
                str(package["archive"]), str(package["archive_sha256"]), cache, work
            )
            binary_archive = source_archive
            if package.get("binary_archive"):
                binary_archive = download(
                    str(package["binary_archive"]),
                    str(package["binary_archive_sha256"]),
                    cache,
                    work,
                )
            package_dir = Path(temporary) / name
            payload = package_dir / "package"
            payload.mkdir(parents=True)

            if package.get("binary"):
                binary = download(
                    str(package["binary"]), str(package["binary_sha256"]), cache, work
                )
                target = payload / str(
                    package.get("binary_path", f"usr/bin/{name}")
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(binary, target)
                target.chmod(0o755)
                header = target.read_bytes()[:4]
                if header == b"\x7fELF":
                    validate_elf(target, staging)
                elif not target.read_bytes()[:160].startswith(b"#!"):
                    raise ValueError(
                        f"{name}: upstream binary is neither ELF nor an executable script"
                    )

            member_hashes = package.get("binary_member_sha256", {})
            for source_name, target_name in package.get("binary_members", {}).items():
                data = read_archive_file(
                    binary_archive,
                    str(source_name),
                    max_bytes=MAX_BINARY_MEMBER_BYTES,
                )
                expected = member_hashes.get(source_name)
                if expected and hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError(f"{name}: checksum mismatch for {source_name}")
                target_relative = PurePosixPath(str(target_name))
                if target_relative.is_absolute() or ".." in target_relative.parts:
                    raise ValueError(f"{name}: unsafe package path {target_name!r}")
                target = payload.joinpath(*target_relative.parts)
                write_payload_file(target, data, 0o755)
                if data[:4] == b"\x7fELF":
                    validate_elf(target, staging)
                elif not data[:160].startswith(b"#!"):
                    raise ValueError(
                        f"{name}: {source_name} is not an ELF binary or script"
                    )

            for source_name, target_name in package.get("binary_files", {}).items():
                data = read_archive_file(binary_archive, str(source_name))
                target_relative = PurePosixPath(str(target_name))
                if target_relative.is_absolute() or ".." in target_relative.parts:
                    raise ValueError(f"{name}: unsafe package path {target_name!r}")
                write_payload_file(payload.joinpath(*target_relative.parts), data, 0o644)

            for source_name, target_name in package.get("files", {}).items():
                data = read_archive_file(
                    source_archive, f"{package['root']}/{source_name}"
                )
                target_relative = PurePosixPath(str(target_name))
                if target_relative.is_absolute() or ".." in target_relative.parts:
                    raise ValueError(f"{name}: unsafe package path {target_name!r}")
                mode = 0o755 if target_relative.parts[:2] == ("usr", "bin") else 0o644
                write_payload_file(payload.joinpath(*target_relative.parts), data, mode)

            license_target = payload / str(package["license_path"])
            write_payload_file(
                license_target,
                read_archive_file(
                    source_archive,
                    f"{package['root']}/{package['license_source']}",
                ),
                0o644,
            )

            for directory in (payload, *payload.rglob("*")):
                if directory.is_dir() and not directory.is_symlink():
                    directory.chmod(0o755)

            paths = {
                path.relative_to(payload).as_posix()
                for path in payload.rglob("*")
                if not path.is_dir() or path.is_symlink()
            }
            collisions = sorted(paths.intersection(owned))
            if collisions:
                raise ValueError(
                    f"{name}: paths already owned by arkpkg-repo: "
                    + ", ".join(collisions[:20])
                )

            recipe = recipe_text(name, package)
            recipe_path = repo_root / "packages/recipes" / name / "ARKPKG"
            recipe_path.parent.mkdir(parents=True, exist_ok=True)
            if recipe_path.exists() and recipe_path.read_text(encoding="utf-8") != recipe:
                raise ValueError(f"existing recipe differs from pinned metadata: {recipe_path}")
            recipe_path.write_text(recipe, encoding="utf-8")
            shutil.copy2(recipe_path, package_dir / "ARKPKG")

            archive_path = repo_root / "packages" / (
                f"{name}-{package['version']}-{package['arch']}.ark"
            )
            temporary_archive = archive_path.with_name(f".{archive_path.name}.tmp")
            try:
                subprocess.run(
                    [
                        str(args.arkpkg_build.resolve()),
                        str(package_dir),
                        "--output",
                        str(temporary_archive),
                    ],
                    check=True,
                )
                archive_size = temporary_archive.stat().st_size
                if archive_size > MAX_ARCHIVE_BYTES:
                    raise ValueError(
                        f"{archive_path.name}: archive is {archive_size} bytes; "
                        f"maximum supported size is {MAX_ARCHIVE_BYTES} bytes"
                    )
                os.replace(temporary_archive, archive_path)
            finally:
                temporary_archive.unlink(missing_ok=True)
            owned.update(paths)
            print(
                f"Built {index}/{len(selected)} {archive_path.name} "
                f"({archive_path.stat().st_size} bytes; {len(paths)} payload paths)",
                flush=True,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument(
        "--source-cache", type=Path, default=Path("/tmp/arkpkg-upstream-cli-cache")
    )
    parser.add_argument("--packages", nargs="+", choices=sorted(PACKAGES))
    return parser.parse_args()


if __name__ == "__main__":
    try:
        build(parse_args())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
