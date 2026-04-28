"""Preapproved domains for WebFetchTool.

These domains are trusted documentation sites that are automatically allowed
without requiring user permission.
"""

from urllib.parse import urlparse

# 130+ trusted documentation domains from the reference project
PREAPPROVED_DOMAINS = {
    # AI/LLM
    "docs.anthropic.com",
    "support.anthropic.com",
    # General web documentation
    "developer.mozilla.org",
    "www.w3.org",
    "html.spec.whatwg.org",
    # Python
    "docs.python.org",
    "pypi.org",
    "packaging.python.org",
    "peps.python.org",
    # Python frameworks
    "docs.djangoproject.com",
    "flask.palletsprojects.com",
    "fastapi.tiangolo.com",
    "www.tornadoweb.org",
    "bottlepy.org",
    # Python libraries
    "numpy.org",
    "pandas.pydata.org",
    "scikit-learn.org",
    "pytorch.org",
    "www.tensorflow.org",
    "keras.io",
    "matplotlib.org",
    "seaborn.pydata.org",
    "scipy.org",
    "requests.readthedocs.io",
    "docs.aiohttp.org",
    # JavaScript/TypeScript
    "www.typescriptlang.org",
    "nodejs.org",
    "deno.land",
    "bun.sh",
    # JavaScript frameworks
    "react.dev",
    "reactjs.org",
    "vuejs.org",
    "angular.io",
    "svelte.dev",
    "nextjs.org",
    "remix.run",
    "nuxt.com",
    # JavaScript libraries
    "lodash.com",
    "momentjs.com",
    "date-fns.org",
    "axios-http.com",
    "jestjs.io",
    "vitest.dev",
    "playwright.dev",
    "www.cypress.io",
    # Build tools
    "webpack.js.org",
    "vitejs.dev",
    "rollupjs.org",
    "esbuild.github.io",
    "parceljs.org",
    # CSS
    "tailwindcss.com",
    "getbootstrap.com",
    "sass-lang.com",
    "lesscss.org",
    # Go
    "go.dev",
    "golang.org",
    "pkg.go.dev",
    # Rust
    "www.rust-lang.org",
    "doc.rust-lang.org",
    "docs.rs",
    "crates.io",
    # Java
    "docs.oracle.com",
    "spring.io",
    "docs.spring.io",
    # C/C++
    "en.cppreference.com",
    "isocpp.org",
    # Ruby
    "www.ruby-lang.org",
    "ruby-doc.org",
    "rubyonrails.org",
    "guides.rubyonrails.org",
    # PHP
    "www.php.net",
    "laravel.com",
    "symfony.com",
    # Databases
    "www.postgresql.org",
    "dev.mysql.com",
    "www.mongodb.com",
    "redis.io",
    "www.sqlite.org",
    "cassandra.apache.org",
    # Cloud platforms
    "docs.aws.amazon.com",
    "cloud.google.com",
    "learn.microsoft.com",
    "docs.microsoft.com",
    # DevOps
    "docs.docker.com",
    "kubernetes.io",
    "www.terraform.io",
    "docs.ansible.com",
    # Version control
    "git-scm.com",
    "docs.github.com",
    "docs.gitlab.com",
    # Package managers
    "www.npmjs.com",
    "yarnpkg.com",
    "pnpm.io",
    "pip.pypa.io",
    "bundler.io",
    "getcomposer.org",
    # Testing
    "junit.org",
    "mochajs.org",
    "jasmine.github.io",
    # API documentation
    "swagger.io",
    "www.openapis.org",
    "graphql.org",
    # Standards
    "www.ecma-international.org",
    "tc39.es",
    "www.json.org",
    "yaml.org",
    "toml.io",
}

def is_preapproved_domain(url: str) -> bool:
    """Check if a URL's domain is in the preapproved list.

    Args:
        url: The URL to check

    Returns:
        True if the domain is preapproved, False otherwise
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False

        # Check exact match
        if hostname in PREAPPROVED_DOMAINS:
            return True

        # Check without www prefix
        if hostname.startswith("www."):
            if hostname[4:] in PREAPPROVED_DOMAINS:
                return True

        # Check with www prefix
        if f"www.{hostname}" in PREAPPROVED_DOMAINS:
            return True

        return False
    except Exception:
        return False
