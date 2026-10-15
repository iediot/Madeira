#!/usr/bin/env python3
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
harness = r'''
#import "ProfileFolders.h"
#include <assert.h>
int main(int argc, char **argv) {
    @autoreleasepool {
        NSFileManager *fm = NSFileManager.defaultManager;
        NSString *prefix = @(argv[1]);
        NSString *home = [prefix stringByAppendingPathComponent:@"drive_c/users/mobile"];
        [fm createDirectoryAtPath:home withIntermediateDirectories:YES attributes:nil error:nil];
        NSString *existing = [home stringByAppendingPathComponent:@"Documents"];
        [fm createDirectoryAtPath:existing withIntermediateDirectories:YES attributes:nil error:nil];
        NSString *save = [existing stringByAppendingPathComponent:@"save.bin"];
        [@"keep" writeToFile:save atomically:YES encoding:NSUTF8StringEncoding error:nil];
        NSString *valid = [home stringByAppendingPathComponent:@"Pictures"];
        assert([fm createSymbolicLinkAtPath:valid withDestinationPath:existing error:nil]);
        NSString *broken = [home stringByAppendingPathComponent:@"Saved Games"];
        assert([fm createSymbolicLinkAtPath:broken withDestinationPath:@"/missing/madeira-test-target" error:nil]);
        assert(madeira_repair_profile_folders(prefix, @[@"mobile", @"madeira", @"mobile", @"..", @"bad/name"]) > 0);
        assert([[NSString stringWithContentsOfFile:save encoding:NSUTF8StringEncoding error:nil] isEqualToString:@"keep"]);
        assert([[fm destinationOfSymbolicLinkAtPath:valid error:nil] isEqualToString:existing]);
        assert([[fm destinationOfSymbolicLinkAtPath:broken error:nil]
                 isEqualToString:@"/missing/madeira-test-target"]);
        assert(![fm fileExistsAtPath:[broken stringByAppendingString:@".madeira-broken-link"]]);
        for (NSString *leaf in @[@"Documents", @"Desktop", @"Downloads", @"Music", @"Pictures",
                                 @"Videos", @"Saved Games", @"AppData/Local", @"AppData/Roaming"]) {
            assert(![fm fileExistsAtPath:[prefix stringByAppendingPathComponent:
                [@"drive_c/users/madeira" stringByAppendingPathComponent:leaf]]]);
        }
        assert([fm fileExistsAtPath:[home stringByAppendingPathComponent:@"AppData/LocalLow"]]);
        NSString *fallback = [prefix stringByAppendingPathComponent:@"drive_c/game/Quantic Dream/progress.qdsav"];
        [fm createDirectoryAtPath:[fallback stringByDeletingLastPathComponent]
              withIntermediateDirectories:YES attributes:nil error:nil];
        [@"progress" writeToFile:fallback atomically:YES encoding:NSUTF8StringEncoding error:nil];
        assert(!madeira_repair_profile_folders(prefix, @[@"mobile", @"madeira"]));
        assert([[NSString stringWithContentsOfFile:fallback encoding:NSUTF8StringEncoding error:nil] isEqualToString:@"progress"]);
        puts("PASS: LocalLow-only repair; known folders stay absent; fallback saves and dangling links preserved");
    }
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-profile-test-') as directory:
    path = Path(directory); source = path / 'profile.m'; source.write_text(harness)
    exe = path / 'profile'
    subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-framework', 'Foundation', '-fsanitize=address,undefined',
                    '-I', str(root / 'app/Madeira'), str(source), '-o', str(exe)], check=True)
    subprocess.run([str(exe), str(path / 'prefix')], check=True)
