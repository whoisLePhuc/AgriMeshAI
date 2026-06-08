from setuptools import setup

setup(
    name="agrimesh-mcp",
    version="0.1.0",
    description="AgriMeshAI MCP Server — IoT tool orchestration for smart agriculture",
    packages=["mcp_server"],
    package_dir={"mcp_server": "."},
    python_requires=">=3.10",
    install_requires=[
        "mcp>=1.0",
        "click>=8.0",
        "pyyaml>=6.0",
    ],
    entry_points={
        "console_scripts": [
            "agrimesh=mcp_server.cli:main",
        ],
    },
)
