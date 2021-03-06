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
from wtforms.fields import StringField, SubmitField, FileField, TextField, SelectField
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
media_dir = config["media"]
first_run=True
barsegments=0
file_count=0
media_dir_split = media_dir.split("/")
show_loc = len(media_dir_split)-1


def build_directory_tree(media_d):
    dir_list = []
    split_r = []
    for r,d,f in os.walk(media_d):
        for file in f:
            if file.endswith(input_file_type):
                split_r = r.split("/")
                show_name = split_r[show_loc]
                if show_name not in dir_list: dir_list.append(show_name)
    dir_list.append("All")                
    dir_list.sort
    return sorted(dir_list)

DIR_CHOICES = build_directory_tree(media_dir)


#initialize progress bar count at 0, using file for interprocess comms
with open('progress.txt', 'w') as f:
    f.write("0")

class BaseForm(FlaskForm):
  def __iter__(self):
    token = self.csrf_token
    yield token

    field_names = {token.name}
    for cls in self.__class__.__bases__:
      for field in cls():
        field_name = field.name
        if field_name not in field_names:
          field_names.add(field_name)
          yield self[field_name]

    for field_name in self._fields:
      if field_name not in field_names:
        yield self[field_name]

class DirectoryForm(BaseForm):
    source_dir = SelectField(label='Directory', choices=[(dir,dir) for dir in DIR_CHOICES])
    submit = SubmitField(label='List Files')
    transcode = SubmitField(label='Transcode')
 

class TranscodeForm(BaseForm):
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
    selected_dir = ""
    source_files = ""
    target_files = {}
    DIR_CHOICES = build_directory_tree(media_dir)

    form = DirectoryForm()

    if request.method == "POST" and not form.transcode.data:
    
        if form.source_dir.data == "All":
            session['source_dir'] = media_dir
        else:
            session['source_dir'] = media_dir + form.source_dir.data
        selected_dir = form.source_dir.data
        full_source_files = get_source_files(session['source_dir'])
        source_files = set_display_files(full_source_files)
        
        if not source_files:
            flash("No Files to Transcode","alert-warning")
            return redirect(url_for('index'))
        else:
            full_source_files.sort()
            target_files = get_target_files(full_source_files)

    if request.method == "POST" and form.transcode.data:
        if form.source_dir.data == "All":
            session['source_dir'] = media_dir
        else:
            session['source_dir'] = media_dir + form.source_dir.data
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
            
    return render_template('index.html', output_type=output_file_type, files=source_files, target_files=target_files, form=form, choices=DIR_CHOICES, selected_dir=selected_dir)


@app.errorhandler(404)
def not_found(e):
    return render_template('404.html')


if __name__ == '__main__':
    app.run(debug=True, threaded=True)
