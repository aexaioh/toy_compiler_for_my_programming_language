#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/time.h>
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <regex.h>

#ifndef MYLANG_SOURCE_FILE
#define MYLANG_SOURCE_FILE "<unknown>"
#endif

typedef struct { int tag; long long i; const char* s; void* p; } MVal;
typedef struct { int status; char* out; char* err; } CmdResult;
typedef struct { int len; int cap; MVal* items; } MList;
typedef struct { const char* key; MVal value; } MapEntry;
typedef struct { int len; int cap; MapEntry* entries; } MMap;

static MList* mylang_list_new(void) {
  MList* l = malloc(sizeof(MList));
  l->len = 0; l->cap = 8;
  l->items = malloc(sizeof(MVal) * l->cap);
  return l;
}

static void mylang_list_push(MList* l, MVal v) {
  if (l->len == l->cap) {
    l->cap *= 2;
    l->items = realloc(l->items, sizeof(MVal) * l->cap);
  }
  l->items[l->len++] = v;
}

static MMap* mylang_map_new(void) {
  MMap* m = malloc(sizeof(MMap));
  m->len = 0; m->cap = 8;
  m->entries = malloc(sizeof(MapEntry) * m->cap);
  return m;
}

static int mylang_map_index(MMap* m, const char* k) {
  for (int i = 0; i < m->len; i++) {
    if (strcmp(m->entries[i].key, k) == 0) return i;
  }
  return -1;
}

static MVal mylang_map_get(MMap* m, const char* k) {
  int i = mylang_map_index(m, k);
  if (i < 0) {
    MVal none = {1, 0, NULL, NULL};
    return none;
  }
  return m->entries[i].value;
}

static MMap* mylang_map_set(MMap* m, const char* k, MVal v) {
  MMap* r = mylang_map_new();
  for (int i = 0; i < m->len; i++) {
    if (r->len == r->cap) { r->cap *= 2; r->entries = realloc(r->entries, sizeof(MapEntry) * r->cap); }
    r->entries[r->len++] = m->entries[i];
  }
  int i = mylang_map_index(r, k);
  if (i >= 0) {
    r->entries[i].value = v;
  } else {
    if (r->len == r->cap) { r->cap *= 2; r->entries = realloc(r->entries, sizeof(MapEntry) * r->cap); }
    r->entries[r->len].key = strdup(k);
    r->entries[r->len].value = v;
    r->len++;
  }
  return r;
}

static int mylang_map_has(MMap* m, const char* k) {
  return mylang_map_index(m, k) >= 0;
}

static int mylang_map_len(MMap* m) {
  return m->len;
}

static MMap* mylang_map_delete(MMap* m, const char* k) {
  MMap* r = mylang_map_new();
  for (int i = 0; i < m->len; i++) {
    if (strcmp(m->entries[i].key, k) == 0) continue;
    if (r->len == r->cap) { r->cap *= 2; r->entries = realloc(r->entries, sizeof(MapEntry) * r->cap); }
    r->entries[r->len++] = m->entries[i];
  }
  return r;
}

static int mylang_cmp_int(const void* a, const void* b) {
  const MVal* x = (const MVal*)a; const MVal* y = (const MVal*)b;
  if (x->i < y->i) return -1;
  if (x->i > y->i) return 1;
  return 0;
}

static int mylang_cmp_str(const void* a, const void* b) {
  const MVal* x = (const MVal*)a; const MVal* y = (const MVal*)b;
  return strcmp(x->s ? x->s : "", y->s ? y->s : "");
}

static int __mylang_argc = 0;
static char** __mylang_argv = NULL;

static void mylang_init_args(int argc, char** argv) {
  __mylang_argc = argc;
  __mylang_argv = argv;
}

static MList* mylang_args(void) {
  MList* l = mylang_list_new();
  for (int i = 1; i < __mylang_argc; i++) {
    MVal v = {0, 0, __mylang_argv[i], NULL};
    mylang_list_push(l, v);
  }
  return l;
}

static MVal mylang_getenv(const char* name) {
  const char* val = getenv(name);
  if (val) {
    MVal v = {0, 0, val, NULL};
    return v;
  }
  MVal v = {1, 0, NULL, NULL};
  return v;
}

static MVal mylang_read_file(const char* path) {
  FILE* f = fopen(path, "rb");
  if (!f) {
    MVal e = {1, 0, strerror(errno), NULL};
    return e;
  }
  fseek(f, 0, SEEK_END);
  long n = ftell(f);
  fseek(f, 0, SEEK_SET);
  char* buf = malloc((size_t)n + 1);
  size_t got = fread(buf, 1, (size_t)n, f);
  buf[got] = 0;
  fclose(f);
  MVal r = {0, 0, buf, NULL};
  return r;
}

static MVal mylang_write_file(const char* path, const char* content) {
  FILE* f = fopen(path, "wb");
  if (!f) {
    MVal e = {1, 0, strerror(errno), NULL};
    return e;
  }
  size_t n = strlen(content);
  fwrite(content, 1, n, f);
  fclose(f);
  MVal r = {0, 0, NULL, NULL};
  return r;
}

static char* mylang_str_dup(const char* s) {
  size_t n = strlen(s);
  char* r = malloc(n + 1);
  memcpy(r, s, n + 1);
  return r;
}

static char* mylang_str_append(char* a, const char* b) {
  size_t la = strlen(a), lb = strlen(b);
  a = realloc(a, la + lb + 1);
  memcpy(a + la, b, lb + 1);
  return a;
}

static char* mylang_int_to_str(long long v) {
  char* r = malloc(32);
  snprintf(r, 32, "%lld", v);
  return r;
}

static char* mylang_bool_to_str(int v) {
  return mylang_str_dup(v ? "true" : "false");
}

static char* mylang_str_trim(const char* s) {
  while (*s && isspace((unsigned char)*s)) s++;
  size_t n = strlen(s);
  while (n > 0 && isspace((unsigned char)s[n-1])) n--;
  char* r = malloc(n + 1);
  memcpy(r, s, n); r[n] = 0;
  return r;
}

static char* mylang_str_upper(const char* s) {
  size_t n = strlen(s);
  char* r = malloc(n + 1);
  for (size_t i = 0; i < n; i++) r[i] = (char)toupper((unsigned char)s[i]);
  r[n] = 0;
  return r;
}

static char* mylang_str_lower(const char* s) {
  size_t n = strlen(s);
  char* r = malloc(n + 1);
  for (size_t i = 0; i < n; i++) r[i] = (char)tolower((unsigned char)s[i]);
  r[n] = 0;
  return r;
}

static int mylang_str_ends_with(const char* s, const char* suffix) {
  size_t ls = strlen(s), lx = strlen(suffix);
  if (lx > ls) return 0;
  return strcmp(s + ls - lx, suffix) == 0;
}

static char* mylang_str_replace(const char* s, const char* old, const char* new_) {
  size_t lo = strlen(old), ln = strlen(new_), ls = strlen(s);
  if (lo == 0) {
    char* r = malloc(ls + 1); memcpy(r, s, ls + 1); return r;
  }
  size_t cap = ls + 1, len = 0;
  char* r = malloc(cap);
  const char* p = s;
  while (*p) {
    const char* f = strstr(p, old);
    if (!f) {
      size_t rem = strlen(p);
      if (len + rem + 1 > cap) { cap = (len + rem + 1) * 2; r = realloc(r, cap); }
      memcpy(r + len, p, rem); len += rem; break;
    }
    size_t chunk = (size_t)(f - p);
    if (len + chunk + ln + 1 > cap) { cap = (len + chunk + ln + 1) * 2; r = realloc(r, cap); }
    memcpy(r + len, p, chunk); len += chunk;
    memcpy(r + len, new_, ln); len += ln;
    p = f + lo;
  }
  r[len] = 0;
  return r;
}

static MList* mylang_str_split(const char* s, const char* sep) {
  MList* l = mylang_list_new();
  size_t lsep = strlen(sep);
  if (lsep == 0) {
    for (size_t i = 0; s[i]; i++) {
      char* c = malloc(2);
      c[0] = s[i]; c[1] = 0;
      MVal v = {0, 0, c, NULL};
      mylang_list_push(l, v);
    }
    return l;
  }
  const char* p = s;
  while (1) {
    const char* f = strstr(p, sep);
    if (!f) {
      MVal v = {0, 0, strdup(p), NULL};
      mylang_list_push(l, v);
      break;
    }
    size_t chunk = (size_t)(f - p);
    char* c = malloc(chunk + 1);
    memcpy(c, p, chunk);
    c[chunk] = 0;
    MVal v = {0, 0, c, NULL};
    mylang_list_push(l, v);
    p = f + lsep;
  }
  return l;
}

static MList* mylang_str_lines(const char* s) {
  MList* l = mylang_list_new();
  const char* p = s;
  while (*p) {
    const char* f = strchr(p, '\n');
    size_t chunk;
    if (!f) chunk = strlen(p);
    else chunk = (size_t)(f - p);
    if (chunk > 0) {
      char* c = malloc(chunk + 1);
      memcpy(c, p, chunk);
      c[chunk] = 0;
      MVal v = {0, 0, c, NULL};
      mylang_list_push(l, v);
    }
    if (!f) break;
    p = f + 1;
  }
  return l;
}

static char* mylang_str_repeat(const char* s, int n) {
  if (n <= 0) return mylang_str_dup("");
  size_t ls = strlen(s);
  size_t total = ls * (size_t)n;
  char* r = malloc(total + 1);
  for (int i = 0; i < n; i++) memcpy(r + i * ls, s, ls);
  r[total] = 0;
  return r;
}

static char* mylang_str_pad_left(const char* s, int n) {
  size_t ls = strlen(s);
  if ((int)ls >= n) return mylang_str_dup(s);
  char* r = malloc(n + 1);
  memset(r, ' ', (size_t)(n - (int)ls));
  memcpy(r + (n - (int)ls), s, ls);
  r[n] = 0;
  return r;
}

static char* mylang_str_pad_right(const char* s, int n) {
  size_t ls = strlen(s);
  if ((int)ls >= n) return mylang_str_dup(s);
  char* r = malloc(n + 1);
  memcpy(r, s, ls);
  memset(r + ls, ' ', (size_t)(n - (int)ls));
  r[n] = 0;
  return r;
}

static MVal mylang_str_to_int(const char* s) {
  const char* start = s;
  while (*start && isspace((unsigned char)*start)) start++;
  if (*start == 0) { MVal v = {1, 0, NULL, NULL}; return v; }
  char* end = NULL;
  long long val = strtoll(start, &end, 10);
  if (end == start) { MVal v = {1, 0, NULL, NULL}; return v; }
  while (*end && isspace((unsigned char)*end)) end++;
  if (*end != 0) { MVal v = {1, 0, NULL, NULL}; return v; }
  MVal v = {0, val, NULL, NULL};
  return v;
}

#define MYLANG_REGEX_CACHE_SIZE 256
typedef struct { char* pattern; regex_t compiled; } RegexEntry;
static RegexEntry __mylang_regex_cache[MYLANG_REGEX_CACHE_SIZE];
static int __mylang_regex_cache_count = 0;

static regex_t* mylang_regex_get(const char* pattern, int line, int col) {
  for (int i = 0; i < __mylang_regex_cache_count; i++) {
    if (strcmp(__mylang_regex_cache[i].pattern, pattern) == 0) {
      return &__mylang_regex_cache[i].compiled;
    }
  }
  if (__mylang_regex_cache_count >= MYLANG_REGEX_CACHE_SIZE) {
    for (int i = 0; i < __mylang_regex_cache_count; i++) {
      regfree(&__mylang_regex_cache[i].compiled);
      free(__mylang_regex_cache[i].pattern);
    }
    __mylang_regex_cache_count = 0;
  }
  regex_t* re = &__mylang_regex_cache[__mylang_regex_cache_count].compiled;
  int rc = regcomp(re, pattern, REG_EXTENDED | REG_NEWLINE);
  if (rc != 0) {
    char errbuf[256];
    regerror(rc, re, errbuf, sizeof(errbuf));
    fprintf(stderr, "error: invalid regex %s: %s\n", pattern, errbuf);
    fprintf(stderr, "  at %s:%d:%d\n", MYLANG_SOURCE_FILE, line, col);
    exit(1);
  }
  __mylang_regex_cache[__mylang_regex_cache_count].pattern = strdup(pattern);
  __mylang_regex_cache_count++;
  return re;
}

static int mylang_regex_matches(const char* s, const char* pattern, int line, int col) {
  regex_t* re = mylang_regex_get(pattern, line, col);
  return regexec(re, s, 0, NULL, 0) == 0;
}

static MList* mylang_regex_find_all(const char* s, const char* pattern, int line, int col) {
  MList* result = mylang_list_new();
  regex_t* re = mylang_regex_get(pattern, line, col);
  const char* p = s;
  int first = 1;
  while (*p) {
    regmatch_t m;
    int flags = first ? 0 : REG_NOTBOL;
    first = 0;
    if (regexec(re, p, 1, &m, flags) != 0) break;
    if (m.rm_so == m.rm_eo) {
      p++;
      first = 0;
      continue;
    }
    size_t len = (size_t)(m.rm_eo - m.rm_so);
    char* match = malloc(len + 1);
    memcpy(match, p + m.rm_so, len);
    match[len] = 0;
    MVal v = {0, 0, match, NULL};
    mylang_list_push(result, v);
    p += m.rm_eo;
    first = 0;
  }
  return result;
}

static char* mylang_regex_replace(const char* s, const char* pattern, const char* repl, int line, int col) {
  regex_t* re = mylang_regex_get(pattern, line, col);
  size_t cap = strlen(s) + 1;
  size_t len = 0;
  char* out = malloc(cap);
  const char* p = s;
  int first = 1;
  while (*p) {
    regmatch_t matches[10];
    int flags = first ? 0 : REG_NOTBOL;
    first = 0;
    if (regexec(re, p, 10, matches, flags) != 0) break;
    if (matches[0].rm_so == matches[0].rm_eo) {
      if (len + 2 > cap) { cap *= 2; out = realloc(out, cap); }
      out[len++] = *p;
      p++;
      first = 0;
      continue;
    }
    size_t prefix_len = (size_t)matches[0].rm_so;
    if (len + prefix_len + 1 > cap) { cap = (len + prefix_len + 1) * 2; out = realloc(out, cap); }
    memcpy(out + len, p, prefix_len);
    len += prefix_len;
    const char* r = repl;
    while (*r) {
      if (*r == '\\' && r[1] >= '0' && r[1] <= '9') {
        int g = r[1] - '0';
        if (matches[g].rm_so >= 0) {
          size_t glen = (size_t)(matches[g].rm_eo - matches[g].rm_so);
          if (len + glen + 1 > cap) { cap = (len + glen + 1) * 2; out = realloc(out, cap); }
          memcpy(out + len, p + matches[g].rm_so, glen);
          len += glen;
        }
        r += 2;
      } else if (*r == '\\' && r[1] == '\\') {
        if (len + 2 > cap) { cap *= 2; out = realloc(out, cap); }
        out[len++] = '\\';
        r += 2;
      } else {
        if (len + 2 > cap) { cap *= 2; out = realloc(out, cap); }
        out[len++] = *r++;
      }
    }
    p += matches[0].rm_eo;
    first = 0;
  }
  size_t tail = strlen(p);
  if (len + tail + 1 > cap) { cap = len + tail + 1; out = realloc(out, cap); }
  memcpy(out + len, p, tail);
  len += tail;
  out[len] = 0;
  return out;
}

static char* mylang_list_join(MList* l, const char* sep) {
  size_t total = 0;
  size_t lsep = strlen(sep);
  for (int i = 0; i < l->len; i++) {
    const char* s = l->items[i].s ? l->items[i].s : "";
    total += strlen(s);
  }
  if (l->len > 0) total += (size_t)(l->len - 1) * lsep;
  char* r = malloc(total + 1);
  r[0] = 0;
  for (int i = 0; i < l->len; i++) {
    if (i > 0) strcat(r, sep);
    const char* s = l->items[i].s ? l->items[i].s : "";
    strcat(r, s);
  }
  return r;
}

static MList* mylang_list_concat(MList* a, MList* b) {
  MList* r = mylang_list_new();
  for (int i = 0; i < a->len; i++) mylang_list_push(r, a->items[i]);
  for (int i = 0; i < b->len; i++) mylang_list_push(r, b->items[i]);
  return r;
}

static int mylang_list_contains_int(MList* l, long long x) {
  for (int i = 0; i < l->len; i++) if (l->items[i].i == x) return 1;
  return 0;
}

static int mylang_list_contains_str(MList* l, const char* x) {
  for (int i = 0; i < l->len; i++) {
    const char* s = l->items[i].s ? l->items[i].s : "";
    if (strcmp(s, x) == 0) return 1;
  }
  return 0;
}

static int mylang_list_contains_bool(MList* l, int x) {
  for (int i = 0; i < l->len; i++) if ((l->items[i].i != 0) == (x != 0)) return 1;
  return 0;
}

static MList* mylang_list_unique_int(MList* src) {
  MList* r = mylang_list_new();
  for (int i = 0; i < src->len; i++) {
    int seen = 0;
    for (int j = 0; j < r->len; j++) {
      if (r->items[j].i == src->items[i].i) { seen = 1; break; }
    }
    if (!seen) mylang_list_push(r, src->items[i]);
  }
  return r;
}

static MList* mylang_list_unique_str(MList* src) {
  MList* r = mylang_list_new();
  for (int i = 0; i < src->len; i++) {
    const char* si = src->items[i].s ? src->items[i].s : "";
    int seen = 0;
    for (int j = 0; j < r->len; j++) {
      const char* sj = r->items[j].s ? r->items[j].s : "";
      if (strcmp(si, sj) == 0) { seen = 1; break; }
    }
    if (!seen) mylang_list_push(r, src->items[i]);
  }
  return r;
}

static long long mylang_div(long long a, long long b, int line, int col) {
  if (b == 0) {
    fprintf(stderr, "error: division by zero\n");
    fprintf(stderr, "  at %s:%d:%d\n", MYLANG_SOURCE_FILE, line, col);
    exit(1);
  }
  return a / b;
}

static long long mylang_mod(long long a, long long b, int line, int col) {
  if (b == 0) {
    fprintf(stderr, "error: modulo by zero\n");
    fprintf(stderr, "  at %s:%d:%d\n", MYLANG_SOURCE_FILE, line, col);
    exit(1);
  }
  return a % b;
}

static long long mylang_abs(long long x) {
  return x < 0 ? -x : x;
}

static long long mylang_min_int(long long a, long long b) {
  return a < b ? a : b;
}

static long long mylang_max_int(long long a, long long b) {
  return a > b ? a : b;
}

static const char* mylang_min_str(const char* a, const char* b) {
  return strcmp(a, b) <= 0 ? a : b;
}

static const char* mylang_max_str(const char* a, const char* b) {
  return strcmp(a, b) >= 0 ? a : b;
}

static const char* mylang_int_to_str_dup(long long x) {
  return mylang_int_to_str(x);
}

static const char* mylang_str_dup_wrap(const char* s) {
  return mylang_str_dup(s);
}

static char* mylang_map_item_int(const char* k, MVal v) {
  char buf[256];
  snprintf(buf, 256, "%s=%lld", k, v.i);
  return mylang_str_dup(buf);
}

static char* mylang_map_item_str(const char* k, MVal v) {
  size_t lk = strlen(k);
  const char* vs = v.s ? v.s : "";
  size_t lv = strlen(vs);
  char* r = malloc(lk + lv + 2);
  memcpy(r, k, lk);
  r[lk] = '=';
  memcpy(r + lk + 1, vs, lv);
  r[lk + 1 + lv] = 0;
  return r;
}

static char* mylang_map_item_bool(const char* k, MVal v) {
  char buf[256];
  snprintf(buf, 256, "%s=%s", k, v.i ? "true" : "false");
  return mylang_str_dup(buf);
}

static char* mylang_map_item_list_str(const char* k, MVal v) {
  MList* l = (MList*)v.p;
  char* joined = l ? mylang_list_join(l, ",") : mylang_str_dup("");
  size_t lk = strlen(k);
  size_t lv = strlen(joined);
  char* r = malloc(lk + lv + 4);
  memcpy(r, k, lk);
  r[lk] = '=';
  r[lk + 1] = '[';
  memcpy(r + lk + 2, joined, lv);
  r[lk + 2 + lv] = ']';
  r[lk + 3 + lv] = 0;
  return r;
}

static char* mylang_map_item_list_int(const char* k, MVal v) {
  MList* l = (MList*)v.p;
  size_t total = 0;
  int n = l ? l->len : 0;
  char** strs = malloc(sizeof(char*) * (n > 0 ? n : 1));
  for (int i = 0; i < n; i++) {
    strs[i] = mylang_int_to_str(l->items[i].i);
    total += strlen(strs[i]) + 1;
  }
  size_t lk = strlen(k);
  char* r = malloc(lk + 2 + total + 2);
  memcpy(r, k, lk);
  r[lk] = '=';
  r[lk + 1] = '[';
  size_t pos = lk + 2;
  for (int i = 0; i < n; i++) {
    if (i > 0) r[pos++] = ',';
    size_t ls = strlen(strs[i]);
    memcpy(r + pos, strs[i], ls); pos += ls;
  }
  r[pos++] = ']'; r[pos] = 0;
  return r;
}

static MList* mylang_map_keys(MMap* m) {
  MList* l = mylang_list_new();
  for (int i = 0; i < m->len; i++) {
    MVal v = {0, 0, m->entries[i].key, NULL};
    mylang_list_push(l, v);
  }
  return l;
}

static MList* mylang_map_values(MMap* m) {
  MList* l = mylang_list_new();
  for (int i = 0; i < m->len; i++) mylang_list_push(l, m->entries[i].value);
  return l;
}

static MList* mylang_map_items(MMap* m, int kind) {
  MList* l = mylang_list_new();
  for (int i = 0; i < m->len; i++) {
    const char* k = m->entries[i].key;
    MVal v = m->entries[i].value;
    char* item;
    if (kind == 0) item = mylang_map_item_int(k, v);
    else if (kind == 1) item = mylang_map_item_str(k, v);
    else if (kind == 2) item = mylang_map_item_bool(k, v);
    else if (kind == 3) item = mylang_map_item_list_str(k, v);
    else item = mylang_map_item_list_int(k, v);
    MVal r = {0, 0, item, NULL};
    mylang_list_push(l, r);
  }
  return l;
}

static void mylang_sleep_ms(long long ms) {
  if (ms > 0) usleep((useconds_t)ms * 1000);
}

static long long mylang_now_ms(void) {
  struct timeval tv;
  gettimeofday(&tv, NULL);
  return (long long)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

static int __mylang_sigpipe_inited = 0;

static CmdResult* mylang_run_cmd_impl(const char* const* argv, int argc, const char* stdin_data) {
  (void)argc;
  if (!__mylang_sigpipe_inited) {
    signal(SIGPIPE, SIG_IGN);
    __mylang_sigpipe_inited = 1;
  }
  CmdResult* r = malloc(sizeof(CmdResult));
  r->status = -1; r->out = NULL; r->err = NULL;

  int have_stdin = (stdin_data != NULL);
  size_t stdin_total = have_stdin ? strlen(stdin_data) : 0;

  int inpipe[2] = {-1, -1};
  int outpipe[2], errpipe[2];
  if (have_stdin) {
    if (pipe(inpipe) != 0) { r->err = strdup("pipe failed"); return r; }
  }
  if (pipe(outpipe) != 0) {
    if (have_stdin) { close(inpipe[0]); close(inpipe[1]); }
    r->err = strdup("pipe failed"); return r;
  }
  if (pipe(errpipe) != 0) {
    if (have_stdin) { close(inpipe[0]); close(inpipe[1]); }
    close(outpipe[0]); close(outpipe[1]);
    r->err = strdup("pipe failed"); return r;
  }
  pid_t pid = fork();
  if (pid < 0) {
    if (have_stdin) { close(inpipe[0]); close(inpipe[1]); }
    close(outpipe[0]); close(outpipe[1]);
    close(errpipe[0]); close(errpipe[1]);
    r->err = strdup("fork failed"); return r;
  }
  if (pid == 0) {
    if (have_stdin) {
      dup2(inpipe[0], 0);
      close(inpipe[0]); close(inpipe[1]);
    }
    dup2(outpipe[1], 1);
    dup2(errpipe[1], 2);
    close(outpipe[0]); close(outpipe[1]);
    close(errpipe[0]); close(errpipe[1]);
    execvp(argv[0], (char* const*)argv);
    fprintf(stderr, "execvp failed: %s", strerror(errno));
    _exit(127);
  }
  if (have_stdin) {
    close(inpipe[0]);
  }
  close(outpipe[1]);
  close(errpipe[1]);

  size_t stdin_pos = 0;
  int stdin_open = have_stdin;
  if (have_stdin && stdin_total == 0) {
    close(inpipe[1]);
    stdin_open = 0;
  }

  size_t out_cap = 4096, out_len = 0;
  size_t err_cap = 4096, err_len = 0;
  char* out_buf = malloc(out_cap);
  char* err_buf = malloc(err_cap);
  out_buf[0] = 0;
  err_buf[0] = 0;
  int out_open = 1, err_open = 1;

  while (out_open || err_open || stdin_open) {
    struct pollfd fds[3];
    int nfds = 0;
    int in_idx = -1, out_idx = -1, err_idx = -1;
    if (stdin_open) {
      in_idx = nfds;
      fds[nfds].fd = inpipe[1];
      fds[nfds].events = POLLOUT;
      fds[nfds].revents = 0;
      nfds++;
    }
    if (out_open) {
      out_idx = nfds;
      fds[nfds].fd = outpipe[0];
      fds[nfds].events = POLLIN;
      fds[nfds].revents = 0;
      nfds++;
    }
    if (err_open) {
      err_idx = nfds;
      fds[nfds].fd = errpipe[0];
      fds[nfds].events = POLLIN;
      fds[nfds].revents = 0;
      nfds++;
    }
    if (nfds == 0) break;
    int pr = poll(fds, nfds, -1);
    if (pr < 0) break;
    if (stdin_open && in_idx >= 0 && (fds[in_idx].revents & (POLLOUT | POLLHUP | POLLERR))) {
      ssize_t n = write(inpipe[1], stdin_data + stdin_pos, stdin_total - stdin_pos);
      if (n <= 0) {
        stdin_open = 0;
        close(inpipe[1]);
      } else {
        stdin_pos += (size_t)n;
        if (stdin_pos >= stdin_total) {
          stdin_open = 0;
          close(inpipe[1]);
        }
      }
    }
    if (out_open && out_idx >= 0 && (fds[out_idx].revents & (POLLIN | POLLHUP | POLLERR))) {
      if (out_len + 4096 > out_cap) { out_cap *= 2; out_buf = realloc(out_buf, out_cap); }
      ssize_t n = read(outpipe[0], out_buf + out_len, out_cap - out_len - 1);
      if (n <= 0) out_open = 0;
      else { out_len += (size_t)n; out_buf[out_len] = 0; }
    }
    if (err_open && err_idx >= 0 && (fds[err_idx].revents & (POLLIN | POLLHUP | POLLERR))) {
      if (err_len + 4096 > err_cap) { err_cap *= 2; err_buf = realloc(err_buf, err_cap); }
      ssize_t n = read(errpipe[0], err_buf + err_len, err_cap - err_len - 1);
      if (n <= 0) err_open = 0;
      else { err_len += (size_t)n; err_buf[err_len] = 0; }
    }
  }

  if (stdin_open) close(inpipe[1]);
  close(outpipe[0]);
  close(errpipe[0]);
  r->out = out_buf;
  r->err = err_buf;
  int status;
  waitpid(pid, &status, 0);
  if (WIFEXITED(status)) r->status = WEXITSTATUS(status);
  else r->status = -1;
  return r;
}

static CmdResult* mylang_run_cmd(const char* const* argv, int argc) {
  return mylang_run_cmd_impl(argv, argc, NULL);
}

static CmdResult* mylang_run_cmd_stdin(const char* const* argv, int argc, const char* stdin_data) {
  return mylang_run_cmd_impl(argv, argc, stdin_data);
}

static CmdResult* mylang_run_sh(const char* cmd) {
  const char* argv[] = { "sh", "-c", cmd, NULL };
  return mylang_run_cmd_impl(argv, 3, NULL);
}

static CmdResult* mylang_run_sh_stdin(const char* cmd, const char* stdin_data) {
  const char* argv[] = { "sh", "-c", cmd, NULL };
  return mylang_run_cmd_impl(argv, 3, stdin_data);
}
