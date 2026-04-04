# SEO Master Connector - Publishing Notes

## Build package
Run from repository root:

```powershell
powershell -ExecutionPolicy Bypass -File adapters/wordpress-plugin/build-package.ps1 -Version 0.2.0
```

Output ZIP:
`adapters/wordpress-plugin/dist/seo-master-connector-0.2.0.zip`

## Release checklist (must pass before publish)
1. **Static checks**
   - `php -l` for every file in `adapters/wordpress-plugin/*.php`, `includes/*.php`, `admin/*.php`.
   - Confirm `readme.txt` version == plugin header version.
2. **Security checks**
   - Verify HMAC validation rejects bad signature, expired timestamp, and wrong `X-Project-ID`.
   - Verify REST endpoints do not expose secrets in responses/logs.
3. **Functional checks**
   - Install ZIP on clean WordPress (latest + one previous minor).
   - Configure options: `Project ID`, `HMAC Secret`, `Max timestamp drift`.
   - Send signed PATCH requests to:
     - `/wp-json/seo-master/v1/meta`
     - `/wp-json/seo-master/v1/schema`
     - `/wp-json/seo-master/v1/interlinks`
   - Confirm changes are applied and rendered in `wp_head`.
4. **Upgrade/rollback checks**
   - Update plugin from previous version and confirm options persist.
   - Uninstall plugin and confirm cleanup from `uninstall.php`.
5. **Packaging checks**
   - ZIP contains only plugin folder files (no `.git`, tests, temp files).
   - Reinstall ZIP from `dist` and rerun smoke checks.

## WordPress.org submission checklist (if publishing to wp.org)
1. Validate `readme.txt` with official readme validator.
2. Prepare banners/icons/screenshots in `assets/` (outside plugin slug dir for SVN).
3. Tag release in SVN (`/tags/<version>`), update `/trunk`.
4. Publish changelog and support policy.

## Required credentials
- **Project ID**: generated in SEO Master platform.
- **WordPress HMAC Secret**: generated in SEO Master platform (shared with Client API Gateway).
- **Internal API key** (optional, for internal fallback routes only): generated in SEO Master deployment env.

No paid third-party key is required by this plugin itself.
