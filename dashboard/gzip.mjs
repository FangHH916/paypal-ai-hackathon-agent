// After the build: a gzip copy of the bundle, which the Star server sends to browsers that accept gzip.
import { readFileSync, writeFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';

const file = new URL('../web/dashboard/star-dashboard.js', import.meta.url);
writeFileSync(new URL('../web/dashboard/star-dashboard.js.gz', import.meta.url), gzipSync(readFileSync(file), { level: 9 }));
