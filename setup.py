from setuptools import find_packages, setup


setup(
    name="codex-official-api-handoff",
    version="0.1.0",
    description="Safe handoff of Codex Desktop conversations between official login and cc-switch/API mode.",
    packages=find_packages("src"),
    package_dir={"": "src"},
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "codex-official-api-handoff=codex_official_api_handoff.cli:main",
            "codex-handoff=codex_official_api_handoff.short_cli:main",
        ]
    },
)
