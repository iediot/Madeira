// Tests the actual queue/metadata implementation with host cache/Metal stubs.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <atomic>
#include <string>
#include <cassert>
struct MVKShaderModuleKey { size_t codeSize, codeHash; };
static NSString* directory;
static std::atomic<unsigned> loads{0}, putCalls{0};
static bool cacheHit=false;
static bool pressure=false;
static bool mvkMadeiraMemoryPressureActive() { return pressure; }
static size_t mvkLibCacheCap() { return 256; }
static NSString* mvkPrecompDirectory(id<MTLDevice>) { return directory; }
static NSString* mvkPrecompKeyString(uint64_t key) { return [NSString stringWithFormat:@"%016llx", (unsigned long long)key]; }
static id<MTLLibrary> mvkLibCacheGet(uint64_t, size_t) { return cacheHit ? (id<MTLLibrary>)[NSObject new] : nil; }
static void mvkLibCachePut(uint64_t, size_t, id<MTLLibrary>) { ++putCalls; }
static id<MTLLibrary> mvkPrecompLoad(id<MTLDevice>, uint64_t key, const std::string&, NSString* capturedDirectory) {
    assert([capturedDirectory isEqualToString:directory]);
    ++loads;
    return key == 3 ? nil : (id<MTLLibrary>)[NSObject new];
}
#include "MVKMadeiraShaderPrewarm.h"
static void drain() { dispatch_sync(mvkShaderPrewarmQueue(), ^{}); }
int main() { @autoreleasepool {
    directory = [NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString];
    MVKShaderModuleKey module{128,42};
    setenv("MVK_MADEIRA_SHADER_PREWARM","0",1);
    mvkShaderPrewarmRequest(nil,module);
    assert(g_mvkWarmPending == 0 && g_mvkWarmRequests == 0);
    setenv("MVK_MADEIRA_SHADER_PREWARM","1",1);
    for(unsigned i=0;i<32;++i) assert(mvkShaderPrewarmReserve());
    assert(!mvkShaderPrewarmReserve());
    g_mvkWarmPending=0;
    assert(!mvkShaderPrewarmValidRecipe(@{@"key": @"../../bad/path.xx", @"length": @128, @"entry": @"main0"}));
    assert(!mvkShaderPrewarmValidRecipe(@{@"key": @"0000000000000001", @"length": @0, @"entry": @"main0"}));
    assert(!mvkShaderPrewarmValidRecipe(@[@1]));
    for(unsigned key=1;key<=3;++key) { mvkShaderPrewarmRemember(nil,module,key,128,"main0"); drain(); }
    dispatch_sync(mvkShaderPrewarmQueue(), ^{
        auto context=mvkShaderPrewarmContext(directory);
        NSArray* variants=context[@"modules"][mvkShaderPrewarmModuleKey(module)];
        assert(variants.count==2 && [variants[0][@"key"] isEqualToString:@"0000000000000003"]);
    });
    mvkShaderPrewarmRequest(nil,module); drain();
    assert(loads==2 && putCalls==1 && g_mvkWarmLoaded==1 && g_mvkWarmPending==0);
    assert(g_mvkWarmDiskMiss==1);
    cacheHit=true;
    mvkShaderPrewarmRequest(nil,module); drain();
    assert(loads==2 && putCalls==1);
    assert(g_mvkWarmCached==2);
    cacheHit=false;
    pressure=true;
    mvkShaderPrewarmRequest(nil,module); drain();
    assert(loads==2 && g_mvkWarmPressureSkipped==2);
    pressure=false;
    mvkShaderPrewarmRequest(nil,{128,99999}); drain();
    assert(g_mvkWarmNoHint==1);
    // Metadata mutation is queue-confined, bounded independently of admission.
    for(unsigned i=0;i<600;++i) { mvkShaderPrewarmRemember(nil,{128,100+i},1,128,"main0"); drain(); }
    dispatch_sync(mvkShaderPrewarmQueue(), ^{
        auto context=mvkShaderPrewarmContext(directory);
        assert([context[@"modules"] count]==512 && [context[@"order"] count]==512);
    });
    dispatch_semaphore_t written=dispatch_semaphore_create(0);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,3*NSEC_PER_SEC),mvkShaderPrewarmQueue(),^{dispatch_semaphore_signal(written);});
    assert(dispatch_semaphore_wait(written,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC))==0);
    NSString* path=[directory stringByAppendingPathComponent:@"module-prefetch-v1.plist"];
    assert([NSFileManager.defaultManager fileExistsAtPath:path] && g_mvkWarmWritesFailed==0);
    dispatch_sync(mvkShaderPrewarmQueue(), ^{
        [mvkShaderPrewarmContexts() removeAllObjects];
        assert([mvkShaderPrewarmContext(directory)[@"modules"] count]==512);
    });
    [NSFileManager.defaultManager removeItemAtPath:directory error:nil];
    dispatch_release(written);
    puts("PASS actual shader prefetch: disable, admission bound, metadata/variants bounds, validation, cache miss, persistence/reload");
} }
