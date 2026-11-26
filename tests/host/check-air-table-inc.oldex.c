static int mad_air_build_tables_ex_body(struct mad_exec *e, const struct mad_rootsig *rs, const UINT64 *root,
                                   const UINT32 (*consts)[64], const struct mad_pso *pso,
                                   UINT cb_bind, UINT arg_bind, UINT arg_qwords, UINT nair,
                                   const struct madeira_ir_air_range *airv, unsigned vis_mask,   /* ml1083: one stage, described explicitly */
                                   const struct mad_air_plan *plan,   /* madeira-bcd air-plan: NULL = resolve each declaration */
                                   obj_handle_t *cb_buf, UINT64 *cb_off,
                                   obj_handle_t *arg_buf, UINT64 *arg_off) {
    struct mad_device *d = e->q->device;
    UINT64 *cb_words = NULL, *arg_words = NULL;
    UINT64 gpu;
    void *cpu;
    unsigned k;
    static unsigned said_fail;

    *cb_buf = *arg_buf = 0; *cb_off = *arg_off = 0;

    if (cb_bind != ~0u) {
        size_t cbz = 8;   /* ml1130: up to the highest constant-buffer entry this stage has */
        for (k = 0; k < nair; k++) if (airv[k].cb_table && ((size_t)airv[k].ptr_offset + 3) * 8 > cbz) cbz = ((size_t)airv[k].ptr_offset + 3) * 8;
        if (!exec_ring_take_z(e, 1, cbz, cb_buf, cb_off, &cpu, &gpu)) return 0;
        cb_words = (UINT64 *)cpu;
    }
    if (arg_bind != ~0u) {
        unsigned nslots = (unsigned)(((UINT64)arg_qwords * 8 + MAD_ARG_SLOT_BYTES - 1) / MAD_ARG_SLOT_BYTES);   /* ml1087 */
        if (nslots > MAD_ARG_RING_BYTES / MAD_ARG_SLOT_BYTES) {
            if (said_fail++ < 8)
                d3d12_log("[madeira-d3d12] ml1008 '%s' wants a %u-qword argument table; a whole ring chunk holds %u\n",
                          pso->vs_name, arg_qwords, (unsigned)(MAD_ARG_RING_BYTES / 8));
            return 0;
        }
        if (!exec_ring_take_z(e, nslots ? nslots : 1, (size_t)arg_qwords * 8, arg_buf, arg_off, &cpu, &gpu)) return 0;   /* ml1130 */
        arg_words = (UINT64 *)cpu;
    }

    for (k = 0; k < nair; k++) {
        const struct madeira_ir_air_range *rg = &airv[k];
        struct mad_descriptor de;
        UINT64 direct = 0;
        const char *why = "?";
        UINT64 *tab = rg->cb_table ? cb_words : arg_words;
        UINT off = rg->ptr_offset;

        if (!tab) { why = "the table this range belongs to was not reported"; goto bad; }
        if (!(plan ? mad_air_route_apply(e, rs, root, consts, rg, &plan->r[k], &de, &direct, &why)
                   : mad_air_resolve(e, rs, root, consts, rg, vis_mask, &de, &direct, &why))) {
            /* If the stage filter is what lost it, the signature's visibility
             * was parsed wrong or the engine relies on lax behaviour: bind as
             * before and COUNT it, rather than trade one skipped draw for another. */
            const char *why2 = "?";
            if (vis_mask == ~0u ||
                !(plan ? mad_air_route_apply(e, rs, root, consts, rg, &plan->r[nair + k], &de, &direct, &why2)
                       : mad_air_resolve(e, rs, root, consts, rg, ~0u, &de, &direct, &why2))) goto bad;
            InterlockedIncrement(&g_vis_fallback);
        }
        if (e->sk && e->sk->tab_on) mad_skin_tok(e, rg, &de, direct);   /* madeira-bcd skin-check */

        if (g_dump_tables)   /* ml1106 */
            d3d12_log("[capture-draw]   %s%u space%u -> %s word %u: va=0x%llx view=%llu meta=0x%llx direct=0x%llx flags=%#x\n",
                      rg->type == 0 ? "b" : rg->type == 1 ? "s" : rg->type == 2 ? "t" : "u", rg->lower_bound, rg->space,
                      rg->cb_table ? "cb-table" : "arg-table", off, (unsigned long long)de.gpu_va, (unsigned long long)de.texture_view_id,
                      (unsigned long long)de.metadata, (unsigned long long)direct, rg->flags);
        if (rg->type == MADEIRA_IR_AIR_CBV) {
            /* One word: the buffer's address. */
            tab[off] = direct ? direct : de.gpu_va;
            if (!tab[off]) {
                /* ml1049: a NULL descriptor in a bound table. D3D12 only requires a
                 * descriptor to be valid if the shader actually reads it, and a real
                 * GPU runs the draw; skipping the whole draw was our invention. A
                 * null CBV reads as zeros, which is what the 64 KB null buffer is. */
                if (!d->null_gpu) { why = "constant buffer resolved to address zero"; goto bad; }
                tab[off] = d->null_gpu;
                InterlockedIncrement(&g_null_cbv_bound);
            }
            continue;
        }
        if (rg->type == MADEIRA_IR_AIR_SAMPLER) {
            /* Three words: sampler id, cube-sampler id, LOD bias. Our sampler
             * descriptor keeps the id in gpu_va and the bias in metadata. */
            tab[off + 0] = de.gpu_va;
            tab[off + 1] = de.texture_view_id ? de.texture_view_id : de.gpu_va;
            tab[off + 2] = de.metadata & 0xffffffffull;
            continue;
        }
        /* SRV and UAV share the encoding; the flags pick which of the three. */
        if (rg->flags & MADEIRA_IR_AIR_F_BUFFER) {
            tab[off + 0] = de.gpu_va;
            tab[off + 1] = de.metadata & 0xffffffffull;          /* byte length */
        } else if (rg->flags & MADEIRA_IR_AIR_F_TEXTURE) {
            if (de.metadata & MAD_DESC_TYPEDBUF) {
                UINT32 count = 0, first = 0;
                if (!mad_air_texbuf_c(e, d, de.texture_view_id, &count, &first)) {
                    why = "typed buffer view not found"; goto bad;
                }
                tab[off + 0] = de.texture_view_id;
                tab[off + 1] = ((UINT64)count << 32) | (UINT64)first;
            } else {
                UINT64 vid = de.texture_view_id; UINT32 alen = 1;
                if (g_flat_views < 0) {
                    g_flat_views = mad_cfg_int_pe("sm5-flat-views", 1) ? 1 : 0;
                    d3d12_log("[madeira-d3d12] ml1308 DXBC texture2d bindings get %s (madeira.cfg sm5-flat-views)\n",
                              g_flat_views ? "2D views of the array textures" : "the array views (old behaviour)");
                }
                mad_air_texinfo_c(e, d, de.texture_view_id, g_flat_views && !(rg->flags & MADEIRA_IR_AIR_F_TEXTURE_ARRAY), &vid, &alen);   /* ml1308 */
                tab[off + 0] = vid;
                tab[off + 1] = ((UINT64)alen << 32)
                             | (de.metadata & 0xffffffffull);     /* min LOD clamp */
            }
        } else {
            why = "range has neither the buffer nor the texture flag"; goto bad;
        }
        if (rg->flags & MADEIRA_IR_AIR_F_UAV_COUNTER) {   /* the view's counter, when it has one */
            static LONG said_ctr;
            tab[off + 2] = (rg->flags & MADEIRA_IR_AIR_F_BUFFER) ? mad_uavctr_get(de.gpu_va) : 0;
            if (!tab[off + 2] && InterlockedIncrement(&said_ctr) <= 8)
                d3d12_log("[madeira-d3d12] UAV counter for '%s' u%u space %u: the view has no counter; the shader gets address 0\n",
                          pso->vs_name, rg->lower_bound, rg->space);
        }
        continue;

    bad:
        mad_skip_why(why);
        if (said_fail++ < 32) {
            static const char *cls[4] = { "b", "s", "t", "u" };
            d3d12_log("[madeira-d3d12] ml1008 '%s': cannot bind %s%u space %u (id %u, flags %#x): %s\n",
                      pso->vs_name, rg->type < 4 ? cls[rg->type] : "?", rg->lower_bound,
                      rg->space, rg->range_id, rg->flags, why);
        }
        return 0;
    }
    return 1;
}
/* Track the roots this shader can actually consume, as opposed to invalidating
 * every stage for every root write. Include all matching visibilities: resolve
 * can retry with visibility ALL, so filtering them here could reuse stale data.
 * This is a conservative dependency set, not a hash of descriptor contents. */
static UINT mad_air_root_mask(const struct mad_rootsig *rs, const struct madeira_ir_air_range *air, UINT n) {
    UINT mask = 0, i, k, j;
    if (!rs || (n && !air)) return ~0u;
    for (i = 0; i < rs->nparams && i < MAD_ROOT_PARAM_MAX; i++) {
        const struct madeira_ir_root_param *p = &rs->params[i];
        for (k = 0; k < n; k++) {
            if (p->type == MADEIRA_IR_PARAM_TABLE) {
                for (j = 0; j < p->num_ranges; j++) {
                    UINT idx = p->first_range + j;
                    const struct madeira_ir_root_range *r;
                    if (idx < p->first_range || idx >= rs->nranges) break;
                    r = &rs->ranges[idx];
                    if (r->range_type == mad_air_range_type(air[k].type) && r->register_space == air[k].space &&
                        air[k].lower_bound >= r->base_register && (r->num_descriptors == ~0u ||
                        air[k].lower_bound - r->base_register < r->num_descriptors)) mask |= 1u << i;
                }
            } else if ((p->type == mad_air_param_type(air[k].type) ||
                        (p->type == MADEIRA_IR_PARAM_CONSTANTS && air[k].type == MADEIRA_IR_AIR_CBV)) &&
                       p->shader_register == air[k].lower_bound && p->register_space == air[k].space) mask |= 1u << i;
        }
    }
    return mask;
}
static int mad_air_roots_equal(UINT mask, const UINT64 *a, const UINT64 *b) {
    unsigned i;
    for (i = 0; i < MAD_ROOT_PARAM_MAX; i++) if ((mask & (1u << i)) && a[i] != b[i]) return 0;
    return 1;
}
static int mad_air_build_tables_ex(struct mad_exec *e, const struct mad_rootsig *rs, const UINT64 *root,
                                   const UINT32 (*consts)[64], const struct mad_pso *pso,
                                   UINT cb_bind, UINT arg_bind, UINT arg_qwords, UINT nair,
                                   const struct madeira_ir_air_range *airv, unsigned vis_mask,   /* ml1083: one stage, described explicitly */
                                   obj_handle_t *cb_buf, UINT64 *cb_off,
                                   obj_handle_t *arg_buf, UINT64 *arg_off) {
    const struct mad_air_plan *plan = mad_air_plan_get(pso, rs, airv, nair, vis_mask);   /* madeira-bcd air-plan */
    if (g_air_cache_on < 0) g_air_cache_on = mad_cfg_int_pe("air-table-cache", 1) ? 1 : 0;   /* perf: an unchanged argument table is bound again (0 = every table rebuilt, for A/B) */
    if (!g_air_cache_on)
        return mad_air_build_tables_ex_body(e, rs, root, consts, pso, cb_bind, arg_bind, arg_qwords, nair, airv, vis_mask,
                                            plan, cb_buf, cb_off, arg_buf, arg_off);
    UINT bp = root == e->croot ? 1 : 0;
    UINT64 rv = e->root_version[bp];
    const UINT64 *pv = e->root_param_version[bp];
    UINT64 sv = mad_descriptor_version(e->srv), mv = mad_descriptor_version(e->smp);
    UINT root_mask = ~0u;
    int have_mask = 0, roots_moved = 0;   /* madeira-bcd: why a lookup missed */
    unsigned i;
    /* madeira-bcd: replay split "of which argument tables" */
    UINT64 t0 = g_replay_split > 0 ? mad_tick() : 0;
    int ok;
    /* The ring allocation lives for the whole submitted batch, so a replay may
     * bind it again until roots, heaps or descriptor contents change. */
    if (!g_dump_tables && !(e->sk && e->sk->tab_on)) for (i = 0; i < 8; i++) {
        const typeof(e->air_cache[0]) *c = &e->air_cache[i];
        if (c->rs == rs && c->pso == pso && c->air == airv && c->cb_bind == cb_bind &&
            c->arg_bind == arg_bind && c->arg_qwords == arg_qwords && c->nair == nair &&
            c->vis_mask == vis_mask && c->root_bp == bp) {
            root_mask = c->root_mask; have_mask = 1;
            if (c->srv != e->srv || c->smp != e->smp || c->srv_version != sv || c->smp_version != mv) continue;
            if (c->root_version != rv && !mad_air_roots_equal(c->root_mask, c->root_param_version, pv)) { roots_moved = 1; continue; }
            *cb_buf = c->cb_buf; *cb_off = c->cb_off;
            *arg_buf = c->arg_buf; *arg_off = c->arg_off;
            InterlockedIncrement64(&g_air_cache_hits);
            if (c->root_version != rv) InterlockedIncrement64(&g_air_cache_root_reuse);
            if (t0) mad_rs_step(RS_TABLES, &t0);
            return 1;
        }
    }
    InterlockedIncrement64(&g_air_cache_misses);
    InterlockedIncrement64(!have_mask ? &g_air_miss_new : roots_moved ? &g_air_miss_root : &g_air_miss_heap);   /* roots: an entry with today's heaps had other roots */
    ok = mad_air_build_tables_ex_body(e, rs, root, consts, pso, cb_bind, arg_bind, arg_qwords, nair, airv, vis_mask,
                                      plan, cb_buf, cb_off, arg_buf, arg_off);
    if (ok && !g_dump_tables && !(e->sk && e->sk->tab_on)) {
        typeof(e->air_cache[0]) *c = &e->air_cache[e->air_cache_next++ & 7];
        c->rs = rs; c->pso = pso; c->air = airv; c->cb_bind = cb_bind; c->arg_bind = arg_bind;
        c->arg_qwords = arg_qwords; c->nair = nair; c->vis_mask = vis_mask;
        c->root_version = rv; c->srv = e->srv; c->smp = e->smp;
        c->root_bp = bp;
        c->root_mask = have_mask ? root_mask : plan ? plan->root_mask : mad_air_root_mask(rs, airv, nair);
        memcpy(c->root_param_version, pv, sizeof c->root_param_version);
        c->srv_version = sv; c->smp_version = mv;
        c->cb_buf = *cb_buf; c->cb_off = *cb_off; c->arg_buf = *arg_buf; c->arg_off = *arg_off;
    }
    if (t0) mad_rs_step(RS_TABLES, &t0);
    return ok;
}
