/*
 * sha256-file: streaming SHA-256 for macOS Recovery.
 * Implements FIPS 180-4 directly; requires only the C runtime in libSystem.
 * Usage: sha256-file FILE | sha256-file --self-test
 * Copyright (c) 2026. Released under the MIT license; see LICENSE.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    uint32_t h[8];
    uint64_t bytes;
    size_t used;
    unsigned char block[64];
} sha256_ctx;

static uint32_t rotr(uint32_t x, unsigned n) {
    return (x >> n) | (x << (32U - n));
}

static void sha256_block(sha256_ctx *ctx, const unsigned char block[64]) {
    static const uint32_t k[64] = {
        0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,
        0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
        0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,
        0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
        0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,
        0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
        0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,
        0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
        0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,
        0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
        0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,
        0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
        0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,
        0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
        0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,
        0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U
    };
    uint32_t w[64], a, b, c, d, e, f, g, h;
    unsigned i;
    for (i = 0; i < 16; ++i) {
        const unsigned char *p = block + i * 4U;
        w[i] = ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16)
             | ((uint32_t)p[2] << 8) | (uint32_t)p[3];
    }
    for (i = 16; i < 64; ++i) {
        uint32_t s0 = rotr(w[i-15],7) ^ rotr(w[i-15],18) ^ (w[i-15] >> 3);
        uint32_t s1 = rotr(w[i-2],17) ^ rotr(w[i-2],19) ^ (w[i-2] >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    a=ctx->h[0]; b=ctx->h[1]; c=ctx->h[2]; d=ctx->h[3];
    e=ctx->h[4]; f=ctx->h[5]; g=ctx->h[6]; h=ctx->h[7];
    for (i = 0; i < 64; ++i) {
        uint32_t s1 = rotr(e,6) ^ rotr(e,11) ^ rotr(e,25);
        uint32_t ch = (e & f) ^ (~e & g);
        uint32_t t1 = h + s1 + ch + k[i] + w[i];
        uint32_t s0 = rotr(a,2) ^ rotr(a,13) ^ rotr(a,22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t t2 = s0 + maj;
        h=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
    }
    ctx->h[0]+=a; ctx->h[1]+=b; ctx->h[2]+=c; ctx->h[3]+=d;
    ctx->h[4]+=e; ctx->h[5]+=f; ctx->h[6]+=g; ctx->h[7]+=h;
}

static void sha256_init(sha256_ctx *ctx) {
    static const uint32_t iv[8] = {
        0x6a09e667U,0xbb67ae85U,0x3c6ef372U,0xa54ff53aU,
        0x510e527fU,0x9b05688cU,0x1f83d9abU,0x5be0cd19U
    };
    memcpy(ctx->h, iv, sizeof iv);
    ctx->bytes = 0;
    ctx->used = 0;
}

static void sha256_update(sha256_ctx *ctx, const unsigned char *p, size_t n) {
    ctx->bytes += (uint64_t)n;
    while (n != 0) {
        size_t take = 64U - ctx->used;
        if (take > n) take = n;
        memcpy(ctx->block + ctx->used, p, take);
        ctx->used += take;
        p += take;
        n -= take;
        if (ctx->used == 64U) {
            sha256_block(ctx, ctx->block);
            ctx->used = 0;
        }
    }
}

static void sha256_final(sha256_ctx *ctx, char hex[65]) {
    static const char digits[] = "0123456789abcdef";
    uint64_t bits = ctx->bytes * UINT64_C(8);
    unsigned i;
    ctx->block[ctx->used++] = 0x80;
    if (ctx->used > 56U) {
        memset(ctx->block + ctx->used, 0, 64U - ctx->used);
        sha256_block(ctx, ctx->block);
        ctx->used = 0;
    }
    memset(ctx->block + ctx->used, 0, 56U - ctx->used);
    for (i = 0; i < 8; ++i) ctx->block[63U-i] = (unsigned char)(bits >> (8U*i));
    sha256_block(ctx, ctx->block);
    for (i = 0; i < 32; ++i) {
        unsigned char b = (unsigned char)(ctx->h[i/4U] >> (24U - 8U*(i%4U)));
        hex[2U*i] = digits[b >> 4];
        hex[2U*i+1U] = digits[b & 15U];
    }
    hex[64] = '\0';
}

static int self_test(void) {
    static const struct { const char *text; const char *digest; } vectors[] = {
        {"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
        {"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"},
        {"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
         "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"}
    };
    sha256_ctx ctx;
    char hex[65];
    unsigned i;
    unsigned char chunk[1000];
    for (i = 0; i < sizeof vectors / sizeof vectors[0]; ++i) {
        sha256_init(&ctx);
        sha256_update(&ctx, (const unsigned char *)vectors[i].text, strlen(vectors[i].text));
        sha256_final(&ctx, hex);
        if (strcmp(hex, vectors[i].digest) != 0) {
            fprintf(stderr, "sha256-file: self-test vector %u failed\n", i);
            return 1;
        }
    }
    memset(chunk, 'a', sizeof chunk);
    sha256_init(&ctx);
    for (i = 0; i < 1000; ++i) sha256_update(&ctx, chunk, sizeof chunk);
    sha256_final(&ctx, hex);
    if (strcmp(hex, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0") != 0) {
        fputs("sha256-file: self-test million-a failed\n", stderr);
        return 1;
    }
    if (puts("SELF_TEST_OK") == EOF || fflush(stdout) == EOF) return 1;
    return 0;
}

int main(int argc, char **argv) {
    FILE *file;
    sha256_ctx ctx;
    unsigned char buffer[32768];
    char hex[65];
    size_t n;
    int saved_errno;
    if (argc != 2) {
        fputs("Usage: sha256-file FILE\n       sha256-file --self-test\n", stderr);
        return 2;
    }
    if (strcmp(argv[1], "--self-test") == 0) return self_test();
    file = fopen(argv[1], "rb");
    if (file == NULL) {
        fprintf(stderr, "sha256-file: %s: %s\n", argv[1], strerror(errno));
        return 1;
    }
    sha256_init(&ctx);
    while ((n = fread(buffer, 1, sizeof buffer, file)) != 0) sha256_update(&ctx, buffer, n);
    if (ferror(file)) {
        saved_errno = errno;
        (void)fclose(file);
        fprintf(stderr, "sha256-file: %s: %s\n", argv[1],
                saved_errno ? strerror(saved_errno) : "read error");
        return 1;
    }
    if (fclose(file) != 0) {
        fprintf(stderr, "sha256-file: %s: %s\n", argv[1], strerror(errno));
        return 1;
    }
    sha256_final(&ctx, hex);
    if (puts(hex) == EOF || fflush(stdout) == EOF) {
        fputs("sha256-file: output error\n", stderr);
        return 1;
    }
    return 0;
}
