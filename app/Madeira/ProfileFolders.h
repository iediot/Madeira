#import <Foundation/Foundation.h>
#include <sys/stat.h>
#include <errno.h>

/* Only create the longstanding LocalLow folder. Creating another known folder
 * can redirect games away from existing install-relative saves. Do not repair
 * dangling links either: their targets may be temporarily unavailable. */
static NSInteger madeira_repair_profile_folders(NSString *prefix, NSArray<NSString *> *users)
{
    NSArray<NSString *> *relative = @[@"AppData/LocalLow"];
    NSFileManager *fm = NSFileManager.defaultManager;
    NSInteger made = 0;
    for (NSString *user in [NSOrderedSet orderedSetWithArray:users]) {
        if (!user.length || [user isEqualToString:@"."] || [user isEqualToString:@".."] ||
            [user rangeOfString:@"/"].location != NSNotFound ||
            [user rangeOfString:@"\\"].location != NSNotFound) continue;
        NSString *home = [prefix stringByAppendingPathComponent:
                          [@"drive_c/users" stringByAppendingPathComponent:user]];
        for (NSString *rel in relative) {
            NSString *path = [home stringByAppendingPathComponent:rel];
            struct stat link;
            if (!lstat(path.fileSystemRepresentation, &link)) {
                continue;
            } else if (errno != ENOENT) continue;
            if ([fm createDirectoryAtPath:path withIntermediateDirectories:YES attributes:nil error:nil]) ++made;
        }
    }
    return made;
}
