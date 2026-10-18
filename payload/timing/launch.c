#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <tlhelp32.h>
#include <wincrypt.h>
#include <stdint.h>
#include <stdio.h>

static BOOL supported_file(const wchar_t *path) {
    const BYTE expected[32]={0x19,0xb3,0x54,0x2a,0x7d,0x2f,0x7a,0x05,0x0b,0x16,0xb2,0xea,0x5e,0x4b,0xa0,0x23,
        0x1b,0x13,0xc3,0xfb,0x7f,0xcd,0xb2,0xc3,0xe8,0x97,0x67,0xd4,0xd3,0x54,0x43,0x0b};
    HCRYPTPROV provider=0; HCRYPTHASH hash=0;
    HANDLE file=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ,NULL,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,NULL);
    BYTE data[65536],digest[32]; DWORD read=0,length=sizeof(digest); BOOL ok=FALSE;
    if(file==INVALID_HANDLE_VALUE) return FALSE;
    if(!CryptAcquireContextW(&provider,NULL,NULL,PROV_RSA_AES,CRYPT_VERIFYCONTEXT) ||
       !CryptCreateHash(provider,CALG_SHA_256,0,0,&hash)) goto done;
    for (;;) {
        if(!ReadFile(file,data,sizeof(data),&read,NULL)) goto done;
        if(!read) break;
        if(!CryptHashData(hash,data,read,0)) goto done;
    }
    ok=CryptGetHashParam(hash,HP_HASHVAL,digest,&length,0) && length==32 && !memcmp(digest,expected,32);
done:
    if(hash) CryptDestroyHash(hash);
    if(provider) CryptReleaseContext(provider,0);
    CloseHandle(file); return ok;
}

int wmain(int argc,wchar_t **argv) {
    if(argc!=3) {fwprintf(stderr,L"Usage: Uprising60.exe GAME_DIRECTORY DLL_PATH\n"); return 2;}
    wchar_t command[2048],dll[MAX_PATH],game[MAX_PATH];
    if(_snwprintf(game,MAX_PATH,L"%ls\\Data\\ra3ep1_1.0.game",argv[1])<0) return 2;
    if(!supported_file(game)) {fputs("Refused: game SHA-256 does not match the supported build.\n",stderr);return 6;}
    DWORD dll_length=GetFullPathNameW(argv[2],MAX_PATH,dll,NULL);
    if(!dll_length||dll_length>=MAX_PATH||GetFileAttributesW(dll)==INVALID_FILE_ATTRIBUTES) return 3;
    if (_snwprintf(command,2048,L"\"%ls\\Data\\ra3ep1_1.0.game\" -config \"%ls\\RA3EP1_chinese_s_1.0.SkuDef\"",argv[1],argv[1]) < 0) return 2;
    STARTUPINFOW startup={.cb=sizeof(startup)}; PROCESS_INFORMATION process={0};
    if(!CreateProcessW(NULL,command,NULL,NULL,FALSE,0,NULL,argv[1],&startup,&process)) {
        fprintf(stderr,"CreateProcess failed %lu\n",GetLastError()); return 4;
    }
    Sleep(800);
    SIZE_T size=(wcslen(dll)+1)*sizeof(wchar_t),written=0;
    void *remote=VirtualAllocEx(process.hProcess,NULL,size,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
    if(!remote||!WriteProcessMemory(process.hProcess,remote,dll,size,&written)||written!=size) goto failure;
    HMODULE kernel=GetModuleHandleW(L"kernel32.dll");
    uintptr_t offset=(uintptr_t)GetProcAddress(kernel,"LoadLibraryW")-(uintptr_t)kernel;
    HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE,process.dwProcessId);
    if(snapshot==INVALID_HANDLE_VALUE) goto failure;
    MODULEENTRY32W module={.dwSize=sizeof(module)}; uintptr_t remote_kernel=0;
    if(Module32FirstW(snapshot,&module)) do {
        if(!_wcsicmp(module.szModule,L"kernel32.dll")) {remote_kernel=(uintptr_t)module.modBaseAddr;break;}
    } while(Module32NextW(snapshot,&module));
    CloseHandle(snapshot); if(!remote_kernel) goto failure;
    HANDLE thread=CreateRemoteThread(process.hProcess,NULL,0,(LPTHREAD_START_ROUTINE)(remote_kernel+offset),remote,0,NULL);
    if(!thread) goto failure;
    DWORD result=0;
    DWORD wait=WaitForSingleObject(thread,15000);
    if(wait!=WAIT_OBJECT_0||!GetExitCodeThread(thread,&result)||!result) {CloseHandle(thread);goto failure;}
    CloseHandle(thread); VirtualFreeEx(process.hProcess,remote,0,MEM_RELEASE);
    fprintf(stdout,"Game PID=%lu, independent DLL loaded at %08lx. Inspect uprising60.log.\n",process.dwProcessId,result);
    CloseHandle(process.hThread); CloseHandle(process.hProcess); return 0;
failure:
    fprintf(stderr,"Injection failed %lu; closing only the game started by this launcher.\n",GetLastError());
    TerminateProcess(process.hProcess,1);
    CloseHandle(process.hThread);CloseHandle(process.hProcess);return 5;
}
