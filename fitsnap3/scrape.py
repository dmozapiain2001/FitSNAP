# <!----------------BEGIN-HEADER------------------------------------>
# ## FitSNAP3
# A Python Package For Training SNAP Interatomic Potentials for use in the LAMMPS molecular dynamics package
#
# _Copyright (2016) Sandia Corporation. Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation, the U.S. Government retains certain rights in this software. This software is distributed under the GNU General Public License_
# ##
#
# #### Original author:
#     Aidan P. Thompson, athomps (at) sandia (dot) gov (Sandia National Labs)
#     http://www.cs.sandia.gov/~athomps
#
# #### Key contributors (alphabetical):
#     Mary Alice Cusentino (Sandia National Labs)
#     Nicholas Lubbers (Los Alamos National Lab)
#     Adam Stephens (Sandia National Labs)
#     Mitchell Wood (Sandia National Labs)
#
# #### Additional authors (alphabetical):
#     Elizabeth Decolvenaere (D. E. Shaw Research)
#     Stan Moore (Sandia National Labs)
#     Steve Plimpton (Sandia National Labs)
#     Gary Saavedra (Sandia National Labs)
#     Peter Schultz (Sandia National Labs)
#     Laura Swiler (Sandia National Labs)
#
# <!-----------------END-HEADER------------------------------------->

import os
import sys
import collections

import json
import numpy as np

import pandas as pd
import natsort

import sklearn
import tqdm

from . import geometry


group_types = (
    ('name',str),
    ('size',float),
    ('eweight',float),
    ('fweight',float),
    ('vweight',float),
)

style_vars = ['AtomType', 'Stress', 'Lattice', 'Energy', "Positions", "Forces"]
array_vars = ['AtomTypes', 'Stress', 'Lattice', "Positions", "Forces"]

def create_smartweights_grouplist(base_path,json_directory):
    json_directory = os.path.join(base_path, json_directory)
    group_list = os.listdir(json_directory)
    num_groups = len(group_list)
    zero_array = np.zeros(shape=(num_groups,5))
    group_table = pd.DataFrame(zero_array,columns=['name','size','eweight','fweight','vweight'])
    group_table['name'] = group_table['name'].astype(str)
    row_count = 0
    for group in group_list:
        group_directory = os.path.join(json_directory,group)
        files = os.listdir(group_directory)
        num_files = len(files)
        num_atoms_group = 0
        for json_file in files:
            json_path = os.path.join(group_directory, json_file)
            with open(json_path) as file:
                comment = file.readline()
                try:
                    data = json.loads(file.read(),parse_constant=True)
                except Exception as e:
                     print("Trouble Parsing Training Data: ",fname)
                     raise e
                current_num_atoms = float(data['Dataset']['Data'][0]['NumAtoms'])
                num_atoms_group += current_num_atoms
        group_name = group
        group_size = num_files
        group_eweight = float(1/num_files)
        group_fweight = float(1/(num_atoms_group*3))
        group_vweight = float(1/(num_files*6))
        group_table.at[row_count, 'name'] = group_name
        group_table.at[row_count, 'size'] = group_size
        group_table.at[row_count, 'eweight'] = group_eweight
        group_table.at[row_count, 'fweight'] = group_fweight
        group_table.at[row_count, 'vweight'] = group_vweight

        row_count += 1

    new_grouplist_path = os.path.join(base_path,'grouplist_smartweights.in')
    new_grouplist_file = open(new_grouplist_path,'w')
    new_grouplist_file.write("#   Grouplist generated using smartweights" + '\n' + '#')
    new_grouplist_file.write(group_table.to_string(index=False))

    new_grouplist_file.close()

    print("Generating new group weights using smartweights....")
    print(group_table)


    return group_table

def read_groups(group_file):
    group_names = [name for name,type in group_types]
    group_table = pd.read_csv(group_file,
                              delim_whitespace=True,
                              comment='#',
                              skip_blank_lines=True,
                              names=group_names,
                              index_col=False)
    # Remove blank lines ; skip_blank_lines doesn't seem to work.
    group_table = group_table.dropna()
    group_table.index = range(len(group_table.index))
    # Convert data types
    group_table = group_table.astype(dtype=dict(group_types))
    return group_table

def read_configs(json_folder,group_table,bispec_options):
    all_data = []
    test_data = []
    BOLTZT = bispec_options["BOLTZT"]
    styles = collections.defaultdict(lambda: set())
    all_index = 0
    test_index = 0
    if bispec_options["units"]=="real":
        kb=0.00198198665029335
    if bispec_options["units"]=="metal":
        kb=0.00008617333262145
    if bispec_options["atom_style"]=="spin":
        style_vars.append("Spins")
        array_vars.append("Spins")
    if bispec_options["atom_style"]=="charge":
        style_vars.append("Charges")
        array_vars.append("Charges")

    for group_info in tqdm.tqdm(group_table.itertuples(),desc="Groups",position=0,total=len(group_table),disable=(not bispec_options["verbosity"]), ascii=True):
        group_name = group_info.name
        folder = os.path.join(json_folder, group_info.name)
        files = os.listdir(folder)
#        assert len(files) == group_info.size, \
#        ("Found a different number of files than what is defined in grouplist.in", group_name)
        #print(group_name)
        if group_info.size>=1.0:
            folder_files=natsort.natsorted(os.listdir(folder))
            nfiles=len(folder_files)
            nfiles_train=nfiles
            nfiles_test=0
            print(group_info.name,": Gathering and fitting whole training set")
        else:
            folder_files=sklearn.utils.shuffle(os.listdir(folder))
            nfiles=len(folder_files)
            nfiles_train=max(1,int(abs(group_info.size)*len(folder_files)-0.5))
            nfiles_test=max(1,int(float(bispec_options["compute_testerrs"])*len(folder_files)-0.5))
            # Rather than sorting, can randomize the list and only take the top X% of training
            assert ((nfiles_train+nfiles_test) <= nfiles), \
                    "Training and Test sets overlap, exiting"
            print(group_info.name,": Training Set: ",nfiles_train ," Test Set: ",nfiles_test)

        for i, fname_end in tqdm.tqdm(enumerate(folder_files),
                                      desc="Configs",position=1,leave=False,total=(nfiles_train+nfiles_test),disable=(not bispec_options["verbosity"]), ascii=True):
            fname = os.path.join(folder, fname_end)
            with open(fname) as file:
                comment = file.readline()
                try:
                    data = json.loads(file.read(),parse_constant=True)
                except Exception as e:
                    print("Trouble Parsing Training Data: ",fname)
                    raise e

            assert len(data) == 1, \
                "More than one object (dataset) is in this file"
            data = data['Dataset']
            assert len(data['Data']) == 1, \
                "More than one configuration in this dataset"

            data['Group'] = group_name
            data['File'] = fname
            data['GroupIndex'] = i
            data['Index'] = all_index

            for sty in style_vars:
                styles[sty].add(data.pop(sty + "Style",))

            assert all(k not in data for k in data["Data"][0].keys()), \
                "Duplicate keys in dataset and data"

            data.update(data.pop('Data')[0])  # Move data up one level
            for key in array_vars:
                data[key] = np.asarray(data[key])

            natoms=np.shape(data["Positions"])[0]
            data["QMLattice"] = data["Lattice"]
            del data["Lattice"] # We will populate this with the lammps-normalized lattice.
            if "Label" in data: del data["Label"]   # This comment line is not that useful to keep around.

            # possibly due to JSON, some configurations have integer energy values.
            if not isinstance(data["Energy"],float):
                tqdm.tqdm.write(
                    f"Warning: Configuration {all_index} ({group_name}/{fname_end}) gives energy as an integer",file=sys.stderr)
                data["Energy"] = float(data["Energy"])

            units_conv = geometry.units_conv(styles,bispec_options)
            data["Energy"] *= units_conv["Energy"]
            data.update(geometry.rotate_coords(data,units_conv))
            data.update(geometry.translate_coords(data,units_conv))

            if getattr(group_info, 'eweight')>=0.0:
                    for wtype in ['eweight', 'fweight', 'vweight']:
                        data[wtype] = getattr(group_info, wtype)
            else:
                data['eweight'] = np.exp((getattr(group_info, 'eweight')-data["Energy"]/float(natoms))/(kb*float(bispec_options["BOLTZT"])))
                data['fweight'] = data['eweight']*getattr(group_info, 'fweight')
                data['vweight'] = data['eweight']*getattr(group_info, 'vweight')

            if i<nfiles_train:
                all_data.append(data)
                all_index += 1
            elif (i==nfiles_train and nfiles_test==0):
                all_data.append(data)
                all_index += 1
                test_data.append(data)
                test_index += 1
            else:
                test_data.append(data)
                test_index += 1

    for style_name, style_set in styles.items():
        assert len(style_set) == 1, "Multiple styles ({}) for {}".format(len(style_set), style_name)

    return all_data, test_data, {k:v.pop() for k,v in styles.items()}
