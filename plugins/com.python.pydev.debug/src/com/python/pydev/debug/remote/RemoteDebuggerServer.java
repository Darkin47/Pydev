package com.python.pydev.debug.remote;

import java.io.IOException;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketException;

import org.eclipse.debug.core.DebugException;
import org.eclipse.debug.core.ILaunch;
import org.eclipse.debug.core.model.IProcess;
import org.python.pydev.core.log.Log;
import org.python.pydev.debug.model.AbstractDebugTarget;
import org.python.pydev.debug.model.PySourceLocator;
import org.python.pydev.debug.model.remote.AbstractRemoteDebugger;

import com.python.pydev.debug.DebugPluginPrefsInitializer;
import com.python.pydev.debug.model.ProcessServer;
import com.python.pydev.debug.model.PyDebugTargetServer;

/**
 * After this class is created once, it will stay alive 'forever', as it will block in the server socket accept.
 * Note that if it for some reason exits (in the case of an exception), the thread will be recreated.
 */
public class RemoteDebuggerServer extends AbstractRemoteDebugger implements Runnable {
    
    /**
     * 0 == infinite timeout.
     */
    private final static int TIMEOUT = 0;
    
    /**
     * The socket that should be used to listen for clients that want a remote debug session.
     */
    private volatile static ServerSocket serverSocket;
    
    /**
     * The launch that generated this debug server 
     */
    private volatile ILaunch launch;
    
    /**
     * Are we terminated?
     * (starts as if it was terminated)
     */
    private volatile boolean terminated = true;
    
    /**
     * An emulation of a process, to make Eclipse happy (and so that we have somewhere to write to).
     */
    private volatile ProcessServer serverProcess;
    
    /**
     * The iprocess that is created for the debug server
     */
    private volatile IProcess iProcess;


    /**
     * Identifies if we're in the middle of a dispose operation (prevent recursive calls).
     */
    private volatile boolean inDispose = false;

    /**
     * Identifies if we're in the middle of a stop listening operation (prevent recursive calls).
     */
    private volatile boolean inStopListening = false;

    /**
     * The remote debugger port being used. 
     */
    private volatile static int remoteDebuggerPort=-1;
    
    /**
     * This is the server
     */
    private volatile static RemoteDebuggerServer remoteServer;
    
    /**
     * The thread for the debug
     */
    private volatile static Thread remoteServerThread;
    
    /**
     * Private (it's a singleton)
     */
    private RemoteDebuggerServer() {    
    }
    
    public static synchronized RemoteDebuggerServer getInstance() {
        if(remoteDebuggerPort != DebugPluginPrefsInitializer.getRemoteDebuggerPort()){
            if(remoteServer != null){
                remoteServer.dispose();
            }
            remoteServer = null;
            remoteServerThread = null;
        }
        if(remoteServer==null) {
            remoteServer = new RemoteDebuggerServer();
        }
        if(remoteServerThread==null) {
            remoteServerThread = new Thread(remoteServer);
            remoteDebuggerPort = DebugPluginPrefsInitializer.getRemoteDebuggerPort();
            if(serverSocket != null){
                try {
                    serverSocket.close();
                } catch (Exception e) {
                    Log.log(e);
                }
            }
            
            try {
                //System.out.println("starting at:"+remoteDebuggerPort);
                serverSocket = new ServerSocket(remoteDebuggerPort);
                serverSocket.setReuseAddress(true);
                serverSocket.setSoTimeout(TIMEOUT);

            } catch (IOException e) {
                throw new RuntimeException(e);
            }

            remoteServerThread.start();
        }
        return remoteServer;
    }
    
    public void run() {
        try {
            //the serverSocket is static, so, if it already existed, let's close it so it can be recreated.
            while(true) {
                //will be blocked here until a client connects (or user starts in another port)
                startDebugging(serverSocket.accept());
            }
        } catch (SocketException e) {        
            //ignore (will create a new one later)
        } catch (Exception e) {        
            Log.log(e);
        }        
    }        
    
    private void startDebugging(Socket socket) throws InterruptedException {        
        try {
            Thread.sleep(1000);
            if(launch!= null) {
                launch.setSourceLocator(new PySourceLocator());
            }
            PyDebugTargetServer target = new PyDebugTargetServer(launch, null, this);
            target.startTransmission(socket);
            target.initialize();
            this.addTarget(target);
        } catch (IOException e) {        
            e.printStackTrace();
        }        
    }

    public synchronized void stopListening() {
        if(terminated || this.inStopListening){
            return;
        }
        this.inStopListening = true;
        try{
            terminated = true;
            try {
                if (launch != null && launch.canTerminate()){
                    launch.terminate();
                }
            } catch (Exception e) {
                Log.log(e);
            }
            launch = null;
        }finally{
            this.inStopListening = false;
        }
    }
    
    public void dispose() {
        if(this.inDispose){
            return;
        }
        
        this.inDispose = true;
        try{
            this.stopListening();
            if(launch != null){
                for (AbstractDebugTarget target : targets) {
                    launch.removeDebugTarget(target);
                    target.terminate();
                    
                }
            }
            targets.clear();
        }finally{
            this.inDispose = false;
        }
    }
    
    public void disconnect() throws DebugException {    
        //dispose() calls terminate() that calls disconnect()
        //but this calls stopListening() anyways (it's responsible for checking if
        //it's already in the middle of something)
        stopListening();
    }

    
    public void setLaunch(ILaunch launch, ProcessServer p, IProcess pro) {
        if(this.launch != null){
            this.stopListening();
        }
        terminated = false; //we have a launch... so, it's not finished
        this.serverProcess = p;
        this.launch = launch;
        this.iProcess = pro;
    }

    public boolean isTerminated() {
        return terminated;
    }

    public IProcess getIProcess() {
        return this.iProcess;
    }

    public ProcessServer getServerProcess() {
        return this.serverProcess;
    }        
}