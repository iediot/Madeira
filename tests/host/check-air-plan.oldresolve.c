static int mad_air_resolve(struct mad_exec *e, const struct mad_rootsig *rs, const UINT64 *root,
                           const UINT32 (*consts)[64], const struct madeira_ir_air_range *rg,
                           unsigned vis_mask,   /* ml1049: bit per madeira_ir_visibility this stage may see */
                           struct mad_descriptor *desc, UINT64 *direct_va, const char **why) {
    UINT want_range = mad_air_range_type(rg->type);
    UINT want_param = mad_air_param_type(rg->type);
    unsigned i, j;

    *direct_va = 0;
    if (!rs) { *why = "no root signature bound"; return 0; }

    for (i = 0; i < rs->nparams && i < MAD_ROOT_PARAM_MAX; i++) {
        const struct madeira_ir_root_param *pp = &rs->params[i];

        /* ml1049: a root signature may give the SAME register to different
         * stages through different parameters. Taking the first match handed a
         * pixel shader the vertex stage's buffer (or its unset zero). */
        if (pp->visibility < 32 && !((1u << pp->visibility) & vis_mask)) continue;

        if (pp->type == MADEIRA_IR_PARAM_TABLE) {
            /* ml1012: a range may declare its offset as
             * D3D12_DESCRIPTOR_RANGE_OFFSET_APPEND (0xffffffff), meaning
             * "immediately after the previous range in this table". Reading the
             * raw field made that a ~4-billion descriptor index, which is
             * exactly why every such constant buffer reported "descriptor
             * address past the end of the heap" (32 times in one run, all of
             * them this). The offset must be accumulated across the table's
             * ranges IN ORDER -- the same rule mad_table_count already applies
             * when it sizes a table. */
            unsigned running = 0;
            for (j = 0; j < pp->num_ranges; j++) {
                unsigned ri = pp->first_range + j;
                const struct madeira_ir_root_range *rr;
                UINT64 table_gpu, slot_gpu;
                struct mad_heap *h;
                UINT64 byte_off;
                UINT idx, range_off;
                if (ri >= rs->nranges) break;   /* ml1009: ranges are blob-sized now */
                rr = &rs->ranges[ri];
                range_off = (rr->table_offset == 0xffffffffu) ? running : rr->table_offset;
                if (rr->num_descriptors != ~0u) running = range_off + rr->num_descriptors;
                if (rr->range_type != want_range) continue;
                if (rr->register_space != rg->space) continue;
                if (rg->lower_bound < rr->base_register) continue;
                if (rr->num_descriptors != ~0u &&
                    rg->lower_bound >= rr->base_register + rr->num_descriptors) continue;

                idx = range_off + (rg->lower_bound - rr->base_register);
                table_gpu = root[i];
                if (!table_gpu) { *why = "descriptor table root argument never set"; return 0; }
                h = (rg->type == MADEIRA_IR_AIR_SAMPLER) ? e->smp : e->srv;
                if (!h || !h->cpu || !h->gpu_address) { *why = "the matching descriptor heap is not bound"; return 0; }
                slot_gpu = table_gpu + (UINT64)idx * sizeof(struct mad_descriptor);
                if (slot_gpu < h->gpu_address) { *why = "descriptor address below the bound heap"; return 0; }
                byte_off = slot_gpu - h->gpu_address;
                if (byte_off % sizeof(struct mad_descriptor)) { *why = "descriptor address is not slot-aligned"; return 0; }
                if (byte_off / sizeof(struct mad_descriptor) >= h->count) { *why = "descriptor address past the end of the heap"; return 0; }
                *desc = h->cpu[byte_off / sizeof(struct mad_descriptor)];
                return 1;
            }
            continue;
        }

        if (pp->type == MADEIRA_IR_PARAM_CONSTANTS) {
            /* Root constants consumed as a constant buffer. The backend wants a
             * POINTER, so the words need GPU-visible storage; copy them into a
             * ring slot whose lifetime already matches the dispatch. */
            UINT n;
            obj_handle_t cb; UINT64 coff, cgpu; void *ccpu;
            if (rg->type != MADEIRA_IR_AIR_CBV) continue;
            if (pp->shader_register != rg->lower_bound || pp->register_space != rg->space) continue;
            n = pp->num_constants > 64 ? 64 : pp->num_constants;
            if (!exec_ring_take_z(e, 1, 256, &cb, &coff, &ccpu, &cgpu)) { *why = "no ring slot for root constants"; return 0; }   /* ml1130 */
            memcpy(ccpu, consts[i], (size_t)n * 4);
            *direct_va = cgpu + coff;
            return 1;
        }

        if (pp->type == want_param) {
            if (pp->shader_register != rg->lower_bound || pp->register_space != rg->space) continue;
            if (!root[i]) { *why = "root descriptor never set"; return 0; }
            /* A root SRV/UAV carries an address and no length, and the backend
             * needs a byte length for buffer bindings. Refuse rather than
             * invent a bound. */
            if (rg->type != MADEIRA_IR_AIR_CBV) { *why = "root SRV/UAV has no length for this binding"; return 0; }
            memset(desc, 0, sizeof *desc);   /* capture logging also prints the unused descriptor */
            *direct_va = root[i];
            return 1;
        }
    }

    /* Static samplers are baked into the shader by the DXIL converter, but the
     * DXBC backend expects them in the table like any other sampler. The root
     * signature already built each one's descriptor (the ml923 table the DXIL
     * path points at); copy it into the slot. Skipping the draw instead left
     * Ghost of Tsushima's intro videos and final image black. */
    if (rg->type == MADEIRA_IR_AIR_SAMPLER) {
        for (i = 0; i < rs->nsamplers && i < 32; i++)
            if (rs->samplers[i].shader_register == rg->lower_bound &&
                rs->samplers[i].register_space == rg->space &&
                (rs->samplers[i].visibility >= 32 || ((1u << rs->samplers[i].visibility) & vis_mask))) {
                if (rs->stab_cpu && rs->stab_cpu[i].gpu_va) {
                    *desc = rs->stab_cpu[i];
                    return 1;
                }
                *why = "static sampler (its Metal sampler state could not be created)";
                return 0;
            }
    }
    *why = "no root-signature entry covers this register";
    return 0;
}

