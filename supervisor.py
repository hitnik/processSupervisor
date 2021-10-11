import subprocess
import json
from abc import abstractmethod
import time
import smtplib
import logging
import logging.config
from logging.handlers import RotatingFileHandler
from urllib import request, error
from email.message import EmailMessage
from threading import Lock, Thread, Event, enumerate
from queue import Queue
import pyautogui
from win32 import win32gui
import psutil

SETTINGS_FILE_PATH = 'settings.json'


class ProcessHandler(Thread):


    def __init__(self, name, path,process_queue, event, logger=None):
        Thread.__init__(self)
        self.name = name
        self.path = path
        self.event = event
        self.process_queue = process_queue
        self.logger = logger
        self._process = None
        self._pid = None

    process = property()
    pid = property()

    @process.getter
    def process(self):
        return self._process

    @process.setter
    def process(self, value):
        self._process = value

    @pid.getter
    def pid(self):
        return self._pid

    @pid.setter
    def pid(self, value):
        self._pid=value

    @abstractmethod
    def wait(self):
        pass

    @abstractmethod
    def terminate_info(self):
        pass

    def run(self):
        print('process \"{}\" run with PID: {}'.format(self.name, self.pid))
        self.logger.info('process \"{}\" run with PID: {}'.format(self.name, self.pid))

        time.sleep(1)
        self.wait()

        print('Process \"%s\" is finished'%self.name)
        self.logger.info('Process \"%s\" is finished'%self.name)
        self.terminate_info()

        self.process_queue.put({self.name : self.path})
        if not self.event.is_set():
            self.event.set()
            self.logger.info('Set event to process queue')


class ProcessHandlerNew(ProcessHandler):

    def __init__(self, name, path, process_queue, event, lock=None, logger=None):
        super().__init__(name, path, process_queue, event, logger=logger)
        with lock:
            self.process = subprocess.Popen(self.path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.pid = self.process.pid
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if self.process.returncode is None:
                    window = win32gui.GetForegroundWindow()
                    active_window_name = win32gui.GetWindowText(window)
                    rect = win32gui.GetWindowRect(window)
                    if active_window_name == 'Telex':
                        buttons = list(pyautogui.locateAllOnScreen('recieve.png'))
                        for button in buttons:
                            x=button[0]+(button[2]//2)
                            y=button[1]+(button[3]//2)
                            if rect[0] < x < rect[2] and rect[1] < y < rect[3]:
                                pyautogui.click(x=button[0]+(button[2]//2), y=button[1]+(button[3]//2), clicks=2, button='left')
                    time.sleep(1)    
        

    def wait(self):
        self.process.wait()

    def terminate_info(self):
        output, error = self.process.communicate()
        print('{} output : {}'.format(self.name, output))
        self.logger.info('{} output : {}'.format(self.name, output))
        print('{} error {}'.format(self.name, error))
        self.logger.info('{} error {}'.format(self.name, error))

class ProcessHandlerExist(ProcessHandler):

    def __init__(self, name, path, process_queue, event, pid=None, logger=None):
        super().__init__(name, path, process_queue, event, logger=logger)
        self.pid = pid
        self.process = psutil.Process(pid=pid)


    def wait(self):
        self.process.wait()

    def terminate_info(self):
        print('{} terminated '.format(self.name))
        self.logger.info('{} terminated '.format(self.name))

class EmailHandler(Thread):

    def __init__(self, settings, email_event, email_queue, logger=None):
        Thread.__init__(self)
        self.settings = settings
        self.email_event = email_event
        self.email_queue = email_queue
        self.logger = logger


    def run(self):
        while True:
            self.email_event.wait()

            print("Установка соединения")
            self.internet_on()

            print("Отправка оповещения")
            self.sendMail()

            print("sending mail")

            if self.email_event.is_set():
                self.email_event.clear()

    def sendMail(self):

        msg = EmailMessage()
        msq_body = self.settings['email message start']+'\n'
        msg['Subject'] = self.settings['email SUBJECT']
        while True:
            if not self.email_queue.empty():
                msq_body += self.email_queue.get()+'\n'
            if self.email_queue.empty():
                break
        msq_body += self.settings['email message end']
        msg.set_content(msq_body)
        msg['From'] = self.settings['email FROM']
        msg['To'] = self.settings['email TO']
        self.logger.info(msg)
        self.logger.info(msq_body)
        server = smtplib.SMTP(self.settings['smtp host'], self.settings['smtp port'])
        if self.settings['use tls']:
            server.starttls()
        server.login(self.settings['smtp user'], self.settings['smtp password'])
        server.send_message(msg)
        server.quit()
        self.logger.info("Message was succesfully sent")

    def internet_on(self):
        try:
            response = request.urlopen('https://www.google.by/', timeout=5)
            self.logger.info('Connection to network is passed')
        except error.URLError:
            self.logger.info("Network error")
            print('\n Ожидание сети 5 секунд \n')
            for i in range(1, 6):
                print(i)
                time.sleep(1)
            self.internet_on()

def initProcessQueue(dict,queue):
    for process in dict['processes']:
        queue.put(process)


def main():

    logger = logging.getLogger("ProcessSupervisor")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler('supervisor.log', mode='a', maxBytes=5 * 1024 * 1024,
                                  backupCount=2, encoding=None, delay=0)
    logger.addHandler(handler)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)

    print('Process controller started')
    logger.info("Process controller started")



    with open(SETTINGS_FILE_PATH, 'r', encoding='utf-8') as file:
        settings_dict = json.loads(file.read(), encoding='utf-8')

    event_process_terminated = Event()
    event_email = Event()
    process_queue = Queue()
    email_queue = Queue()
    lock = Lock()

    emailHandler = EmailHandler(settings_dict, event_email, email_queue, logger=logger)
    emailHandler.setDaemon(True)
    emailHandler.start()

    proc_list = []

    for p in psutil.process_iter():
        try:
            proc_list.append((p.pid, p.exe()))
        except psutil.AccessDenied as e:
            pass

    for _ in range(len(settings_dict['processes'])):
        item = settings_dict['processes'].pop(0)
        for name, path in item.items():
            filtered = tuple(filter(lambda x: x[1] == path, proc_list))
            if filtered:
                ProcessHandlerExist(name, path, process_queue,
                                      event_process_terminated, logger=logger,
                                      pid=filtered[0][0]).start()
            elif settings_dict['processes']:
                settings_dict['processes'].append({name: path})
            else:
                settings_dict['processes'] = [{name: path}]

   
    initProcessQueue(settings_dict, process_queue)

    while True:
        while True:
            item = process_queue.get()
            if type(item) is dict:
                for process_name,process_path in item.items():
                    ProcessHandlerNew(process_name, process_path, process_queue,
                                    event_process_terminated,lock=lock, logger=logger).start()
                    email_queue.put(process_name)

            time.sleep(1)
            if process_queue.empty():
                break

        print("All processes are run now")
        event_email.set()
        event_process_terminated.wait()
        time.sleep(10)

    emailHandler.join()


if __name__ == '__main__':
    main()
