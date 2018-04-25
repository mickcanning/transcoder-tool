import time
import re
import subprocess
import sys
import os
import json
import datetime
import progressbar
from sse import ServerSentEvent

from flask import Flask, render_template, session, redirect, url_for, Response, request, flash
from flask_bootstrap import Bootstrap
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, FileField
from wtforms.validators import Required, Length
import yaml

app = Flask(__name__)
app.config['SECRET_KEY'] = 'Micks Very Secret Secret'
bootstrap = Bootstrap(app)

APP_CONFIG = "config.yml"
def read_config_from_yaml(filename=APP_CONFIG):
  config = None
  with open(filename, "r") as stream:
    try:
      config = yaml.load(stream)
    except yaml.YAMLError as e:
      print(e)
  return config

config = read_config_from_yaml()  

hbcli = config["hbcli"]
input_file_type = config["input_file_type"] 
output_file_type = config["output_file_type"]
delete_original = config["delete_original"]
preset = config["preset"]
first_run=True
barsegments=0
file_count=0

#initialize progress bar count at 0, using file for interprocess comms
with open('progress.txt', 'w') as f:
    f.write("0")

class DirectoryForm(FlaskForm):
    source_directory = StringField('Source Directory', validators=[Required(),
                                                         Length(1, 100)])
    new_source_directory = MultipleFileField()
    submit = SubmitField(label='Submit')
    transcode = SubmitField(label='Transcode')


class TranscodeForm(FlaskForm):
    transcode = SubmitField(label='Start Transcode')


def get_source_files(source_d):
    file_list = []
    if source_d is not None:
        for r,d,f in os.walk(source_d):
            for file in f:
                if file.endswith(input_file_type):
                    file_list.append(os.path.join(r,file))
    return file_list

def get_target_files(source_files):
    target_files = {}
    for file in source_files:
        file_name = file.split("/")
        short_file_name = file_name[len(file_name)-1]
        pre, ext = os.path.splitext(short_file_name)
        target_file = pre + output_file_type
        target_files[short_file_name] = target_file
    return target_files

def set_display_files(files_list):
    display_files = []
    for file in files_list:
        full_path_file = file.split("/")
        display_files.append(full_path_file[len(full_path_file)-1])
    return display_files

def transcode_files(source_d):
    if source_d is not None:
        pattern = re.compile(
            r"Encoding: task \d+ of \d+, (\d+\.\d\d) % "
            r"\((\d+\.\d\d) fps, avg (\d+\.\d\d) fps, ETA (\d\dh\d\dm\d\ds)\)")

        source_files = get_source_files(source_d)
        source_files.sort()
        barsegments = 100/len(source_files)
        
        file_count=1 


        for file in source_files:
            prog_seg = (barsegments*(file_count-1))
            with open('progress.txt', 'w') as f:
                f.write(str(prog_seg))
            print("\nProcessing: " + file)
            pre, ext = os.path.splitext(file)
            target_file = pre + output_file_type
            command=hbcli + ' -i ' + '"'+file + '"' + ' -o ' + '"' + target_file + '"' + ' --preset=' + preset
            p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            
            while True:
                output = p.stdout.readline()
                if output == '' and p.poll() is not None:
                    break
                if output and pattern.match(output):
                    line_split = output.split(" ")
                    prog_int = int(float(line_split[5]))
                    prog_seg = (barsegments*(file_count-1)) + int(barsegments*(prog_int/100))
                    with open('progress.txt', 'w') as f:
                        f.write(str(prog_seg))
                    session['prog_int'] = prog_seg

            rc = p.wait()
            if rc == 0 and delete_original:
                if os.path.exists(file):
                    try:
                        os.remove(file)
                    except OSError as e:
                        print("Error: %s - %s." % (e.filename, e.strerror))
                else:
                    print("File not found: %s " % file)
            file_count = file_count+1

        with open('progress.txt', 'w') as f:
            f.write("0")

    return True

@app.route('/transcode', methods=['GET'])
def progress():
    def gen():
        with open('progress.txt', 'r') as f:
            prog=(f.readline())
            ev = ServerSentEvent(str(prog))
        yield ev.encode()
    return Response(gen(), mimetype='text/event-stream')


@app.route('/', methods=['GET', 'POST'])
def index():
    session['source_dir'] = None
    source_files = ""
    target_files = {}

    form = DirectoryForm()
    if form.validate_on_submit() and form.submit.data:
        session['source_dir'] = form.source_directory.data
        full_source_files = get_source_files(session['source_dir'])
        source_files = set_display_files(full_source_files)
        print(form.data)
        if not source_files:
            flash("No Files to Transcode","alert-warning")
            return redirect(url_for('index'))
        else:
            full_source_files.sort()
            target_files = get_target_files(full_source_files)
    else:
        if form.validate_on_submit() and form.transcode.data:
            session['source_dir'] = form.source_directory.data
            full_source_files = get_source_files(session['source_dir'])
            source_files = set_display_files(full_source_files)
            if not source_files:
                flash(u'No Files to Transcode', 'alert-warning')
                return redirect(url_for('index'))
            else:
                target_files = get_target_files(full_source_files)    
                tcode=transcode_files(session['source_dir'])
                if tcode:
                    flash("Files Transcoded","alert-success")

    return render_template('index.html', output_type=output_file_type, files=source_files, target_files=target_files, form=form)


@app.errorhandler(404)
def not_found(e):
    return render_template('404.html')


if __name__ == '__main__':
    app.run(debug=True, threaded=True)
