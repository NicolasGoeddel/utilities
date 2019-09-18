#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import psutil
import cStringIO
import shutil
import subprocess
import tempfile

def __getFreePort(start = 10000) :
    listener = set([e.laddr.port for e in psutil.net_connections() if e.status == 'LISTEN'])
    freePort = start
    while freePort in listener :
        freePort += 1
    
    return freePort

def makeTmpFile(input, suffix = '', prefix = '') :

    tmpFile = tempfile.NamedTemporaryFile('wb', suffix = suffix, prefix = prefix, delete = False)
    tmpfileName = tmpFile.name

    if isinstance(input, cStringIO.InputType) or (hasattr(input, 'read') and hasattr(input, 'seek')):
        input.seek(0)
        shutil.copyfileobj(input, tmpFile)
        tmpFile.close()
    
    elif isinstance(input, (str, basestring)) :
        if not '\0' in input and os.path.isfile(input) :
            with openfile(input, 'rb') as hInput :
                shutil.copyfileobj(hInput, tmpFile)
        
        else :
            tmpFile.write(input)
    
    else :
        raise Exception("NotImplemented: Can not create a tempoary file out of '%s'." % type(obj))
    
    return tmpfileName

def __getUnoConvArgs(format, inputFileName, outputFileName) :
    freePort = __getFreePort()
    args = [os.path.join(os.path.dirname(os.path.realpath(__file__)), 'unoconv'),
            '--port', '%d' % freePort,
            '-f', format,
            '-u', 'file://%s/libreoffice_port%d' % (tempfile.gettempdir(), freePort),
            '-o', outputFileName, inputFileName]
    return args

def __genericConverter(input, targetFormat, inputExtension, outputExtension) :
    inputFileName = makeTmpFile(input, suffix = '.' + inputExtension)
    
    outputFile = tempfile.NamedTemporaryFile('w+b', suffix = '.' + outputExtension, delete = False)
    outputFileName = outputFile.name
    
    unoArgs = __getUnoConvArgs(targetFormat, inputFileName, outputFileName)
    
    print(unoArgs)
    
    if subprocess.call(unoArgs) != 0 :
        outputFile.close()
        os.unlink(inputFileName)
        raise RuntimeError("unoconv hat sich mit einer Fehlermeldung beendet. Das PDF konnte nicht erzeugt werden.")
    
    outputFile.close()
    
    result = cStringIO.StringIO()
    with open(outputFileName, 'rb') as hOutput :
        shutil.copyfileobj(hOutput, result)
    
    result.seek(0)
    
    os.unlink(outputFileName)
    os.unlink(inputFileName)
    
    return result

def odt2pdf(input) :
    """ @input darf folgendes sein:
         - Dateiname
         - Bin?rer String
         - StringIO-Objekt
         - File-Objekt
libreoffice.py        
        Ergebnis der Funktion ist ein StringIO-Objekt. Den Inhalt kriegt man mit getvalue()
    """
    
    return __genericConverter(input, 'pdf', 'odt', 'pdf')
